#!/usr/bin/env python
from setuptools import setup, find_packages

install_requires = [
    'sentry>=4.10.0',
]

f = open('README.rst')
readme = f.read()
f.close()

setup(
    name='sentry-jira',
    version='0.5',
    author='Adam Thurlow',
    author_email='thurloat@gmail.com',
    url='http://github.com/thurloat/sentry-jira',
    description='A Sentry extension which creates JIRA issues from sentry events.',
    long_description=readme,
    license='BSD',
    install_requires=install_requires,
    include_package_data=True,
    zip_safe=False,
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Operating System :: OS Independent',
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python',
        'Framework :: Django',
        'Topic :: Software Development'
    ],
)