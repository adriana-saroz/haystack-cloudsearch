import os
from setuptools import setup, find_packages

def read(fname):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)) as f:
        return f.read()

setup(
        name='haystack-cloudsearch',
        version='0.3',
        description='An Amazon Cloudsearch backend for Haystack',
        long_description=read('README.rst'),
        classifiers=[
            'Development Status :: 4 - Beta',
            'Intended Audience :: Developers',
            'License :: OSI Approved :: Apache Software License',
            'Topic :: Internet :: WWW/HTTP :: Indexing/Search',
            'Framework :: Django',
        ],
        author='Brandon Adams',
        author_email='emidln@gmail.com',
        url='https://github.com/pbs/haystack-cloudsearch',
        license='Apache License (2.0)',
        py_modules=['haystack_cloudsearch'],
        packages=find_packages(),
        include_package_data=True,
)
