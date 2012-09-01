import logging
import requests
import collections
from sentry.utils import json
from sentry.utils.cache import cache
from simplejson.decoder import JSONDecodeError
from BeautifulSoup import BeautifulStoneSoup

log = logging.getLogger(__name__)

CACHE_KEY = "SENTRY-JIRA-%s"

class JIRAClient(object):
    """
    The JIRA API Client, so you don't have to.
    """

    PROJECT_URL = '/rest/api/2/project'
    META_URL = '/rest/api/2/issue/createmeta'
    CREATE_URL = '/rest/api/2/issue'
    PRIORITIES_URL = '/rest/api/2/priority'
    VERSIONS_URL = '/rest/api/2/project/%s/versions'

    def __init__(self, instance_uri, username, password):
        self.instance_url = instance_uri
        self.username = username
        self.password = password

    def get_projects_list(self):
        return self.get_cached(self.PROJECT_URL)

    def get_create_meta(self, project):
        return self.make_request('get', self.META_URL, {'projectKeys': project, 'expand': 'projects.issuetypes.fields'})

    def get_versions(self, project):
        return self.get_cached(self.VERSIONS_URL % project)

    def get_priorities(self):
        return self.get_cached(self.PRIORITIES_URL)

    def create_issue(self, raw_form_data):
        data = {'fields': raw_form_data}
        return self.make_request('post', self.CREATE_URL, payload=data)

    def make_request(self, method, url, payload=None):
        if url[:4] != "http":
            url = self.instance_url + url
        auth = self.username, self.password
        headers = {'content-type': 'application/json'}
        try:
            if method is 'get':
                r = requests.get(url, params=payload, auth=auth, headers=headers)
            else:
                r = requests.post(url, data=json.dumps(payload), auth=auth, headers=headers)
            return JIRAResponse(r.text, r.status_code)
        except Exception, e:
            logging.error('Error in request to %s: %s' % (url, e.message))
            return JIRAResponse("There was a problem reaching %s: %s" % (url, e.message), 500)

    def get_cached(self, full_url):
        """
        Basic Caching mechanism for requests and responses. It only caches responses
        based on URL
        TODO: Implement GET attr in cache as well. (see self.create_meta for example)
        """
        key = CACHE_KEY % full_url
        cached_result = cache.get(key)
        if not cached_result:
            cached_result = self.make_request('get', full_url)
            cache.set(key, cached_result)
        return cached_result


class JIRAResponse(object):
    """
    A Slimy little wrapper around a python-requests response object that renders
    JSON from JIRA's ordered dicts (fields come back in order, but python obv.
    doesn't care)
    """
    def __init__(self, response_text, status_code):
        self.text = response_text
        self.xml = None
        try:
            self.json = json.loads(response_text, object_pairs_hook=collections.OrderedDict)
        except JSONDecodeError, e:
            if self.text[:5] == "<?xml":
                # perhaps it's XML?
                self.xml = BeautifulStoneSoup(self.text)
            # must be an awful code.
            self.json = None
        self.status_code = status_code