import urllib
import urlparse
from BeautifulSoup import BeautifulStoneSoup
from django import forms
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.utils.translation import ugettext_lazy as _
from sentry.models import GroupMeta
from sentry.plugins.base import register, Response
from sentry.plugins.bases.issue import IssuePlugin
from sentry.utils import json
from forms import JIRAOptionsForm, JIRAIssueForm
from sentry_jira.jira import JIRAClient

@register
class JIRAPlugin(IssuePlugin):
    author = "Adam Thurlow"
    author_url = "https://github.com/thurloat/sentry_jira"
    version = "0.1a"

    slug = "jira"
    title = _("JIRA")
    cont_title = title
    conf_key = slug
    project_conf_form = JIRAOptionsForm
    project_conf_template = "sentry_jira/project_conf.html"
    new_issue_form = JIRAIssueForm
    create_issue_template = 'sentry_jira/create_jira_issue.html'

    def is_configured(self, request, project, **kwargs):
        if not self.get_option('default_project', project):
            return False
        return True

    def get_jira_client(self, project):
        instance = self.get_option('instance_url', project)
        username = self.get_option('username', project)
        pw = self.get_option('password', project)
        return JIRAClient(instance, username, pw)

    def get_initial_form_data(self, request, group, event):
        initial = {
            'summary': self._get_group_title(request, group, event),
            'description': self._get_group_description(request, group, event),
            'project_key': self.get_option('default_project', group.project),
            'jira_client': self.get_jira_client(group.project)
        }

        return initial

    def get_new_issue_title(self):
        return "Create JIRA Issue"

    def create_issue(self, request, group, form_data):
        """
        Since this is called wrapped in a try/catch on ValidationError to display
        an end user error, that's what I'll throw when JIRA doesn't like it.
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

    def get_issue_url(self, group, issue_id):
        instance = self.get_option('instance_url', group.project)
        return "%s/browse/%s" % (instance, issue_id)

    def view(self, request, group, **kwargs):
        """
        Overriding the super to alter the error checking functionality. Method
        source had to be copied in an altered, see huge comment below for changes.
        """
        if not self.is_configured(project=group.project, request=request):
            return self.render(self.not_configured_template)

        if request.GET.get("user_autocomplete"):
            return self.handle_autocomplete(request, group, **kwargs)

        prefix = self.get_conf_key()
        event = group.get_latest_event()

        form = self.new_issue_form(
            request.POST or None,
            initial=self.get_initial_form_data(request, group, event),
            ignored_fields=self.get_option("ignored_fields", group.project))
        ########################################################################
        # to allow the form to be submitted, but ignored so that dynamic fields
        # can change if the issuetype is different
        #
        if request.POST and request.POST.get("changing_issuetype") == "0":
        ########################################################################
            if form.is_valid():
                try:
                    issue_id, error = self.create_issue(
                        group=group,
                        form_data=form.cleaned_data,
                        request=request,
                    )
                    if error:
                        form.errors.update(error)

                except forms.ValidationError, e:
                    form.errors['__all__'] = [u'Error creating issue: %s' % e]

            if form.is_valid():
                GroupMeta.objects.set_value(group, '%s:tid' % prefix, issue_id)

                return self.redirect(reverse('sentry-group', args=[group.project_id, group.pk]))
        else:
            for name, field in form.fields.items():
                form.errors[name] = form.error_class()

        context = {
            'form': form,
            'title': self.get_new_issue_title(),
            }

        return self.render(self.create_issue_template, context)

    def handle_autocomplete(self, request, group, **kwargs):
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
            del query['issueKey'] # some reason JIRA complains if this key is in the URL.
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

class JSONResponse(Response):
    """
    Hack through the builtin response reliance on plugin.render for responses
    by making a plain response out of a subclass of the expected type.
    """
    def __init__(self, object):
        self.object = object

    def respond(self, *args, **kwargs):
        return HttpResponse(json.dumps(self.object), mimetype="application/json")