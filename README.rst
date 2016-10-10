sentry-jira
===========

**DEPRECATED:** This project now lives in `sentry-plugins <https://github.com/getsentry/sentry-plugins>`_

A flexible extension for Sentry which allows you to create issues in JIRA based on sentry events.
It is capable of rendering and saving many custom fields, and will display the proper fields depending on
which issue type you are trying to create.

**Requires Sentry 8+**

Installation
------------

Install the package via ``pip``:

::

    pip install sentry-jira

Configuration
-------------

Go to your project's configuration page (Dashboard -> [Project] -> Settings), select the
Issue Tracking tab, and then click the JIRA button under available integrations.

Enter the JIRA credentials and Project configuration and save changes. Filling out the form is
a two step process (one to fill in data, one to enter additional options).

More Documentation
------------------

Have a look at the readthedocs page for more detailed configuration steps and a
changelog: https://sentry-jira.readthedocs.io/en/latest/

License
-------

sentry-jira is licensed under the terms of the 3-clause BSD license.

Contributing
------------

All contributions are welcome, including but not limited to:

 - Documentation fixes / updates
 - New features (requests as well as implementations)
 - Bug fixes (see issues list)
 - Update supported JIRA types as you come across them

