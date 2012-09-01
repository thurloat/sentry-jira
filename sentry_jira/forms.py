import logging
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from django import forms
from jira import JIRAClient

log = logging.getLogger(__name__)

class JIRAOptionsForm(forms.Form):
    instance_url = forms.CharField(
        label=_("JIRA Instance URL"),
        widget=forms.TextInput(attrs={'class': 'span6', 'placeholder': 'e.g. "https://jira.atlassian.com"'}),
        help_text=_("It must be visible to the Sentry server")
    )
    username = forms.CharField(
        label=_("Username"),
        widget=forms.TextInput(attrs={'class': 'span6'}),
        help_text=_("Ensure the JIRA user has admin perm. on the project")
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'class': 'span6'}),
        help_text=_("Only enter a value if you wish to change it"),
        required=False
    )
    default_project = forms.ChoiceField(
        label=_("Linked Project"),
    )
    ignored_fields = forms.CharField(
        label=_("Ignored Fields"),
        widget=forms.Textarea(attrs={'class': 'span11', 'placeholder': 'e.g. "components, security, customfield_10006"'}),
        help_text=_("Comma-separated list of properties that you don't want to show in the form"),
        required=False
    )

    def __init__(self, *args, **kwargs):
        super(JIRAOptionsForm, self).__init__(*args, **kwargs)

        initial = kwargs.get("initial")
        project_safe = False
        if initial:
            # make a connection to JIRA to fetch a default project.
            jira = JIRAClient(initial.get("instance_url"), initial.get("username"), initial.get("password"))
            projects_response = jira.get_projects_list()
            if projects_response.status_code == 200:
                projects = projects_response.json
                if projects:
                    project_choices = [(p.get('key'), "%s (%s)" % (p.get('name'), p.get('key'))) for p in projects]
                    project_safe = True
                    self.fields["default_project"].choices = project_choices

        if not project_safe:
            del self.fields["default_project"]
            del self.fields["ignored_fields"]

    def clean_password(self):
        """
        Don't complain if the field is empty and a password is already stored,
        no one wants to type a pw in each time they want to change it.
        """
        pw = self.cleaned_data.get("password")
        if pw:
            return pw
        else:
            old_pw = self.initial.get("password")
            if not old_pw:
                raise ValidationError("A Password is Required")
            return old_pw

    def clean(self):
        """
        try and build a JIRAClient and make a random call to make sure the
        configuration is right.
        """
        cd = self.cleaned_data
        jira = JIRAClient(cd["instance_url"], cd["username"], cd["password"])
        sut_response = jira.get_priorities()
        if sut_response.status_code == 403 or sut_response.status_code == 401:
            self.errors["username"] = ["Username might be incorrect"]
            self.errors["password"] = ["Password might be incorrect"]
            raise ValidationError("Unable to connect to JIRA: %s, if you have "
                                  "tried and failed multiple times you may have"
                                  " to enter a CAPTCHA in JIRA to re-enable API"
                                  " logins." % sut_response.status_code)
        elif sut_response.status_code == 500 or sut_response.json is None:
            raise ValidationError("Unable to connect to JIRA: Bad Response")
        elif sut_response.status_code > 200:
            raise ValidationError("Unable to connect to JIRA: %s" % sut_response.status_code)

        return cd


class JIRAIssueForm(forms.Form):

    project_key = forms.CharField(widget=forms.HiddenInput())
    project = forms.CharField(widget=forms.HiddenInput())
    issuetype = forms.ChoiceField(
        label="Issue Type",
        help_text="Changing the issue type will refresh the page with the required form fields."
    )

    summary = forms.CharField(
        label=_("Issue Summary"),
        widget=forms.TextInput(attrs={'class': 'span6'})
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"class": 'span6'})
    )
    fixVersions = forms.MultipleChoiceField(
        label=_("Fix Version/s")
    )

    def __init__(self, *args, **kwargs):
        ignored_fields = kwargs.pop("ignored_fields")
        initial = kwargs.get("initial")
        jira_client = initial.pop("jira_client")

        priorities = jira_client.get_priorities().json
        versions = jira_client.get_versions(initial.get("project_key")).json

        # fetch the meta information about the project.
        meta = jira_client.get_create_meta(initial.get("project_key")).json
        project = meta["projects"][0]
        issue_types = project["issuetypes"]

        # check if the issuetype was passed as a GET parameter
        self.issue_type = initial.get("issuetype")
        if self.issue_type:
            matching_type = [t for t in issue_types if t["id"] == self.issue_type]
            self.issue_type = matching_type[0] if len(matching_type) > 0 else None

        # still no issue type? just use the first one.
        if not self.issue_type:
            self.issue_type = issue_types[0]

        # set back after we've played with the inital data
        kwargs["initial"] = initial

        # call the super to bind self.fields from the defaults.
        super(JIRAIssueForm, self).__init__(*args, **kwargs)

        self.fields["project"].initial = project["id"]
        self.fields["issuetype"].choices = self.make_choices(issue_types)
        self.fields["fixVersions"].choices = self.make_choices(versions)

        # apply ordering to fields based on some known built-in JIRA fields.
        # otherwise weird ordering occurs.
        anti_gravity = {"priority": -150, "components": -100, "security": -50 }
        dynamic_fields = self.issue_type.get("fields").keys()
        dynamic_fields.sort(key=lambda f: anti_gravity.get(f) or 0)
        # build up some dynamic fields based on required shit.
        for field in dynamic_fields:
            if field in self.fields.keys() or field in [x.strip() for x in ignored_fields.split(",")]:
                # don't overwrite the fixed fields for the form.
                continue
            mb_field = self.build_dynamic_field(self.issue_type["fields"][field])
            if mb_field:
                # apply field to form
                self.fields[field] = mb_field

        if "priority" in self.fields.keys():
            # whenever priorities are available, put the available ones in the list.
            # allowedValues for some reason doesn't pass enough info.
            self.fields["priority"].choices = self.make_choices(priorities)


    make_choices = lambda self, x: [(y["id"], y["name"]) for y in x] if x else []

    def clean_description(self):
        """
        Turn code blocks that are in the stack trace into JIRA code blocks.
        """
        desc = self.cleaned_data["description"]
        return desc.replace("```", "{code}")

    def clean(self):
        """
        The form clean method needs to take advantage of the loaded issue type
        fields and meta info so it can determine the format that the datatypes
        should render as.
        """
        very_clean = self.cleaned_data
        fs = self.issue_type["fields"]
        for field in fs.keys():
            f = fs[field]
            if field in ["description", "summary"]:
                continue
            if field in very_clean.keys():
                v = very_clean.get(field)
                if v:
                    schema = f["schema"]
                    if schema["type"] == "user" or schema.get('item') == "user":
                        v = {"name": v}
                    elif schema["type"] == "array" and schema.get("item") != "string":
                        v = [{"id": vx} for vx in v]
                    elif schema.get("custom") == "com.atlassian.jira.plugin.system.customfieldtypes:textarea":
                        v = v
                    elif schema.get("item") != "string":
                        v = {"id": v}
                    very_clean[field] = v
                else:
                    # We don't want to pass blank data back to the API, so kill
                    # None values
                    del very_clean[field]

        # cleanup form data from API
        del very_clean["project_key"]

        return very_clean


    def build_dynamic_field(self, field_meta):
        """
        Builds a field based on JIRA's meta field information
        """
        schema = field_meta["schema"]
        # set up some defaults for form fields
        fieldtype = forms.CharField
        fkwargs = {
            'label': field_meta["name"],
            'required': field_meta["required"],
            'widget': forms.TextInput(attrs={'class': 'span6'})
        }

        # override defaults based on field configuration
        if schema["type"] in ["securitylevel", "priority"]:
            fieldtype= forms.ChoiceField
            fkwargs["choices"] = self.make_choices(field_meta.get('allowedValues'))
            fkwargs["widget"] = forms.Select()
        elif schema.get("items") == "user" or schema["type"] == "user":
            # TODO: Implement user autocompletes.
            fkwargs["widget"] = forms.TextInput(attrs={'class': 'user-selector', 'data-autocomplete': field_meta.get("autoCompleteUrl")})
        elif schema["type"] in ["timetracking"]:
            # TODO: Implement timetracking (currently unsupported alltogether)
            return None
        elif schema.get("items") in ["worklog", "attachment"]:
            # TODO: Implement worklogs and attachments someday
            return None
        elif schema["type"] == "array" and schema["items"] != "string":
            fieldtype = forms.MultipleChoiceField
            fkwargs["choices"] = self.make_choices(field_meta.get("allowedValues"))
            fkwargs["widget"] = forms.SelectMultiple()

        # break this out, since multiple field types could additionally
        # be configured to use a custom property instead of a default.
        if schema.get("custom"):
            if schema["custom"] == "com.atlassian.jira.plugin.system.customfieldtypes:textarea":
                fkwargs["widget"] = forms.Textarea(attrs={'class': 'span6'})

        return fieldtype(**fkwargs)