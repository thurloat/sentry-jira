import logging
import urllib
import urlparse

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.utils.translation import ugettext_lazy as _
from sentry.models import GroupMeta
from sentry.plugins.base import Response
from sentry.plugins.bases.issue import IssuePlugin
from sentry.utils import json

from sentry_jira import VERSION as PLUGINVERSION
from sentry_jira.forms import JIRAOptionsForm, JIRAIssueForm
from sentry_jira.jira import JIRAClient


class JIRAPlugin(IssuePlugin):
    author = "Adam Thurlow"
    author_url = "https://github.com/thurloat/sentry-jira"
    version = PLUGINVERSION

    slug = "jira"
    title = _("JIRA")
    conf_title = title
    conf_key = slug
    project_conf_form = JIRAOptionsForm
    project_conf_template = "sentry_jira/project_conf_form.html"
    new_issue_form = JIRAIssueForm
    create_issue_template = 'sentry_jira/create_jira_issue.html'

    # Adding resource links for forward compatibility, still need to integrate
    # into existing `project_conf.html` template.
    resource_links = [
        ("Documentation", "http://sentry-jira.readthedocs.org/en/latest/"),
        ("README", "https://raw.github.com/thurloat/sentry-jira/master/README.rst"),
        ("Bug Tracker", "https://github.com/thurloat/sentry-jira/issues"),
        ("Source", "http://github.com/thurloat/sentry-jira"),
    ]

    def is_configured(self, request, project, **kwargs):
        if not self.get_option('default_project', project):
            return False
        return True

    def get_jira_client(self, project):
        instance = self.get_option('instance_url', project)
        username = self.get_option('username', project)
        pw = self.get_option('password', project)
        return JIRAClient(instance, username, pw)

    def get_initial_form_data(self, request, group, event, **kwargs):
        initial = {
            'summary': self._get_group_title(request, group, event),
            'description': self._get_group_description(request, group, event),
        }

        default_priority = self.get_option('default_priority', group.project)
        if default_priority:
            initial['priority'] = default_priority

        default_issue_type = self.get_option('default_issue_type', group.project)

        if default_issue_type:
            initial['issuetype'] = default_issue_type

        return initial

    def get_new_issue_title(self):
        return "Create JIRA Issue"

    def create_issue(self, request, group, form_data, **kwargs):
        """
        Form validation errors recognized server-side raise ValidationErrors,
        but when validation errors occur in JIRA they are simply attached to
        the form.
        """
        jira_client = self.get_jira_client(group.project)
        issue_response = jira_client.create_issue(form_data)

        if issue_response.status_code in [200, 201]: # weirdly inconsistent.
            return issue_response.json.get("key"), None
        else:
            # return some sort of error.
            errdict = {"__all__": None}
            if issue_response.status_code == 500:
                errdict["__all__"] = "JIRA Internal Server Error."
            elif issue_response.status_code == 400:
                for k in issue_response.json["errors"].keys():
                    errdict[k] = [issue_response.json["errors"][k],]
                errdict["__all__"] = issue_response.json["errorMessages"]
            else:
                errdict["__all__"] = "Something went wrong, Sounds like a configuration issue: code %s" % issue_response.status_code
            return None, errdict

    def get_issue_url(self, group, issue_id, **kwargs):
        instance = self.get_option('instance_url', group.project)
        return "%s/browse/%s" % (instance, issue_id)

    def view(self, request, group, **kwargs):
        """
        Overriding the super to alter the error checking functionality. Method
        source had to be copied in an altered, see huge comment below for changes.
        """
        has_auth_configured = self.has_auth_configured()
        if not (has_auth_configured and self.is_configured(project=group.project, request=request)):
            if self.auth_provider:
                providers = settings.AUTH_PROVIDERS if hasattr(
                    settings, 'AUTH_PROVIDERS') else settings.SENTRY_AUTH_PROVIDERS
                required_auth_settings = providers[self.auth_provider]
            else:
                required_auth_settings = None

            return self.render(self.not_configured_template, {
                'title': self.get_title(),
                'project': group.project,
                'has_auth_configured': has_auth_configured,
                'required_auth_settings': required_auth_settings,
                })

        if self.needs_auth(project=group.project, request=request):
            return self.render(self.needs_auth_template, {
                'title': self.get_title(),
                'project': group.project,
                })

        if GroupMeta.objects.get_value(group, '%s:tid' % self.get_conf_key(), None):
            return None

        #######################################################################
        # Auto-complete handler
        if request.GET.get("user_autocomplete"):
            return self.handle_user_autocomplete(request, group, **kwargs)
        #######################################################################

        prefix = self.get_conf_key()
        event = group.get_latest_event()

        # Added the ignored_fields to the new_issue_form call
        form = self.new_issue_form(
            request.POST or None,
            initial=self.get_initial_form_data(request, group, event),
            jira_client=self.get_jira_client(group.project),
            project_key=self.get_option('default_project', group.project),
            ignored_fields=self.get_option("ignored_fields", group.project))
        #######################################################################
        # to allow the form to be submitted, but ignored so that dynamic fields
        # can change if the issuetype is different
        #
        if request.POST and request.POST.get("changing_issuetype") == "0":
        #######################################################################
            if form.is_valid():
                issue_id, error = self.create_issue(
                    group=group,
                    form_data=form.cleaned_data,
                    request=request,
                )
                if error:
                    form.errors.update(error)

                    # Register field errors which were returned from the JIRA
                    # API, but were marked as ignored fields in the
                    # configuration with the global error reporter for the form
                    ignored_errors = [v for k, v in error.items()
                                      if k in form.ignored_fields.split(",")]
                    if len(ignored_errors) > 0:
                        errs = form.errors['__all__']
                        errs.append("Validation Error on ignored field, check"
                                    " your plugin settings.")
                        errs.extend(ignored_errors)
                        form.errors['__all__'] = errs

            if form.is_valid():
                GroupMeta.objects.set_value(group, '%s:tid' % prefix, issue_id)

                return self.redirect(reverse('sentry-group', args=[group.team.slug, group.project_id, group.pk]))
        else:
            for name, field in form.fields.items():
                form.errors[name] = form.error_class()

        context = {
            'form': form,
            'title': self.get_new_issue_title(),
            }

        return self.render(self.create_issue_template, context)

    def handle_user_autocomplete(self, request, group, **kwargs):
        """
        Auto-complete JSON handler, Tries to handle multiple different types of
        response from JIRA as only some of their backend is moved over to use
        the JSON REST API, some of the responses come back in XML format and
        pre-rendered HTML.
        """

        url = urllib.unquote_plus(request.GET.get("user_autocomplete"))
        parsed = list(urlparse.urlsplit(url))
        query = urlparse.parse_qs(parsed[3])

        if "/rest/api/latest/user/" in url:  # its the JSON version of the autocompleter
            isXML = False
            query["username"] = request.GET.get('q')
            query.pop('issueKey', False) # some reason JIRA complains if this key is in the URL.
            query["project"] = self.get_option('default_project', group.project)
        else: # its the stupid XML version of the API.
            isXML = True
            query["query"] = request.GET.get("q")
            if query.get('fieldName'):
                query["fieldName"] = query["fieldName"][0] # for some reason its a list.

        parsed[3] = urllib.urlencode(query)
        final_url = urlparse.urlunsplit(parsed)

        jira_client = self.get_jira_client(group.project)
        autocomplete_response = jira_client.get_cached(final_url)
        users = []

        if isXML:
            for userxml in autocomplete_response.xml.findAll("users"):
                users.append({
                    'value': userxml.find("name").text,
                    'display': userxml.find("html").text,
                    'needsRender':False,
                    'q': request.GET.get('q')
                })
        else:
            for user in autocomplete_response.json:
                users.append({
                    'value': user["name"],
                    'display': "%s - %s (%s)" % (user["displayName"], user["emailAddress"], user["name"]),
                    'needsRender': True,
                    'q': request.GET.get('q')
                })

        return JSONResponse({'users': users})

    def handle_issue_type_autocomplete(self, request, group):
        project = request.GET("project")
        jira_client = self.get_jira_client(group.project)
        meta = jira_client.get_meta_for_project(project)

        issue_types = []
        for issue_type in meta.json:
                issue_types.append({
                    'value': issue_type["name"],
                    'display': issue_type["name"],
                    'needsRender': True,
                    'q': request.GET.get('q')
                })

        return issue_types

    def should_create(self, group, event, is_new):

        if GroupMeta.objects.get_value(group, '%s:tid' % self.get_conf_key(), None):
            return False

        auto_create = self.get_option('auto_create', group.project)

        if auto_create and is_new:
            return True

    def post_process(self, group, event, is_new, is_sample, **kwargs):
        if self.should_create(group, event, is_new):

            jira_client = self.get_jira_client(group.project)

            project_key = self.get_option('default_project', group.project)

            project = jira_client.get_create_meta_for_project(project_key)

            if project:
                post_data = {'project': {'id': project['id']}}

            initial = self.get_initial_form_data({}, group, event)

            post_data['summary'] = initial['summary']
            post_data['description'] = initial['description']

            interface = event.interfaces.get('sentry.interfaces.Exception')

            if interface:
                post_data['description'] += "\n{code}%s{code}" % interface.get_stacktrace(event, system_frames=False, max_frames=settings.SENTRY_MAX_STACKTRACE_FRAMES)

            default_priority = initial.get('priority')
            default_issue_type = initial.get('issuetype')

            if not default_priority or not default_issue_type:
                raise Exception("Default priority and issue type not configured...cannot auto create JIRA ticket.")

            post_data['priority'] = {'id': default_priority}
            post_data['issuetype'] = {'id': default_issue_type}

            issue_id, error = self.create_issue(
                request={},
                group=group,
                form_data=post_data)

            if issue_id and not error:
                prefix = self.get_conf_key()
                GroupMeta.objects.set_value(group, '%s:tid' % prefix, issue_id)

            elif error:
                logging.exception("Error creating JIRA ticket: %s" % error)

class JSONResponse(Response):
    """
    Hack through the builtin response reliance on plugin.render for responses
    by making a plain response out of a subclass of the expected type.
    """
    def __init__(self, object):
        self.object = object

    def respond(self, *args, **kwargs):
        return HttpResponse(json.dumps(self.object), mimetype="application/json")
