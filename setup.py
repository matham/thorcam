from setuptools import setup, find_packages
from io import open
from os import path
from os.path import join, isdir
from os import listdir, environ

from thorcam import __version__
from setuptools.dist import Distribution


class BinaryDistribution(Distribution):
    """Wheels need to be generated for each arch.
    """

    def is_pure(self):
        return False

    def has_ext_modules(self):
        return True


here = path.abspath(path.dirname(__file__))

with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

URL = 'https://github.com/matham/thorcam'


def get_wheel_data():
    data = []
    deps = environ.get('THORCAM_WHEEL_DEPS')
    if deps and isdir(deps):
        data.append(
            ('share/thorcam/bin', [join(deps, f) for f in listdir(deps)])
        )
    return data


setup(
    name='thorcam',
    version=__version__,
    author='Matthew Einhorn',
    author_email='moiein2000@gmail.com',
    license='MIT',
    description=(
        'Python interface to the .NET Thor cameras.'),
    long_description=long_description,
    long_description_content_type='text/markdown',
    url=URL,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
    packages=find_packages(),
    install_requires=['ffpyplayer', 'pythonnet', 'numpy', 'ruamel.yaml'],
    data_files=get_wheel_data(),
    distclass=BinaryDistribution,
    project_urls={
        'Bug Reports': URL + '/issues',
        'Source': URL,
    },
)
