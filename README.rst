sentry-jira
===========

An extension for Sentry which allows you to create issues in JIRA.

Installation
------------

Install the package via ``pip``:

::

    pip install sentry-jira

Add ``sentry-jira`` to your ``INSTALLED_APPS`` in your ``sentry.conf.py``:

::

    from sentry.conf.server import *

    INSTALLED_APPS += (
        'sentry_jira',
    )

Configuration
-------------

Go to your project's configuration page (Projects -> [Project]) and select the
JIRA tab. Enter the JIRA credentials and Project configuration and save changes.
Filling out the form is a two step process (one to fill in data, one to select
project).