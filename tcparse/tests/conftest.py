import pytest
import pathlib

from .. import parse


TEST_ROOT = pathlib.Path(__file__).parent


@pytest.fixture(params=list(str(fn) for fn in TEST_ROOT.glob('**/*.tsproj')))
def project_filename(request):
    return request.param


@pytest.fixture(scope='function')
def project(project_filename):
    return parse(project_filename)
