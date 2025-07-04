# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build and test configuration file.

Assumes ``nox >= 2018.9.14`` is installed.
"""

import os
import pathlib
import re
import shutil
import signal
import subprocess

import nox

LOCAL_DEPS = ("google-api-core", "google-cloud-core")
NOX_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_INTERPRETER = "3.8"
ALL_INTERPRETERS = ("3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13")
CURRENT_DIRECTORY = pathlib.Path(__file__).parent.absolute()

BLACK_VERSION = "black==22.3.0"
UNIT_TEST_STANDARD_DEPENDENCIES = [
    "mock",
    "asyncmock",
    "pytest",
    "pytest-cov",
    "google-cloud-testutils",
    "google-cloud-core",
]

# Error if a python version is missing
nox.options.error_on_missing_interpreters = True


def get_path(*names):
    return os.path.join(NOX_DIR, *names)


def install_unittest_dependencies(session, *constraints):
    standard_deps = UNIT_TEST_STANDARD_DEPENDENCIES
    session.install(*standard_deps, *constraints)
    session.install("-e", ".", *constraints)


def default(session):
    # Install all test dependencies, then install this package in-place.
    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )
    install_unittest_dependencies(session, "-c", constraints_path)
    # Run py.test against the unit tests.
    session.run(
        "py.test",
        "--quiet",
        f"--junitxml=unit_{session.python}_sponge_log.xml",
        "--cov=google",
        "--cov=tests/unit",
        "--cov-append",
        "--cov-config=.coveragerc",
        "--cov-report=",
        "--cov-fail-under=0",
        os.path.join("tests", "unit"),
        *session.posargs,
    )


@nox.session(python="3.13")
@nox.parametrize(
    "protobuf_implementation",
    ["python", "upb", "cpp"],
)
def prerelease_deps(session, protobuf_implementation):
    """Run all tests with prerelease versions of dependencies installed."""

    if protobuf_implementation == "cpp" and session.python in ("3.11", "3.12", "3.13"):
        session.skip("cpp implementation is not supported in python 3.11+")

    # Install all dependencies
    session.install("-e", ".[all, tests, tracing]")
    unit_deps_all = UNIT_TEST_STANDARD_DEPENDENCIES
    session.install(*unit_deps_all)

    # Because we test minimum dependency versions on the minimum Python
    # version, the first version we test with in the unit tests sessions has a
    # constraints file containing all dependencies and extras.
    with open(
        CURRENT_DIRECTORY / "testing" / f"constraints-{ALL_INTERPRETERS[0]}.txt",
        encoding="utf-8",
    ) as constraints_file:
        constraints_text = constraints_file.read()

    # Ignore leading whitespace and comment lines.
    constraints_deps = [
        match.group(1)
        for match in re.finditer(
            r"^\s*(\S+)(?===\S+)", constraints_text, flags=re.MULTILINE
        )
    ]

    session.install(*constraints_deps)

    prerel_deps = [
        "protobuf",
        # dependency of grpc
        "six",
        "grpc-google-iam-v1",
        "google-cloud-datastore",
        "googleapis-common-protos",
        "grpcio",
        "grpcio-status",
        "google-api-core",
        "google-auth",
        "proto-plus",
        "google-cloud-testutils",
        # dependencies of google-cloud-testutils"
        "click",
    ]

    for dep in prerel_deps:
        session.install("--pre", "--no-deps", "--upgrade", dep)

    # Remaining dependencies
    other_deps = [
        "requests",
    ]
    session.install(*other_deps)

    # Print out prerelease package versions
    session.run(
        "python", "-c", "import google.protobuf; print(google.protobuf.__version__)"
    )
    session.run("python", "-c", "import grpc; print(grpc.__version__)")
    session.run("python", "-c", "import google.auth; print(google.auth.__version__)")

    session.run(
        "py.test",
        "tests/unit",
        env={
            "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": protobuf_implementation,
        },
    )


@nox.session(python=ALL_INTERPRETERS)
def unit(session):
    """Run the unit test suite."""
    default(session)


@nox.session(py=DEFAULT_INTERPRETER)
def cover(session):
    """Run the final coverage report.

    This outputs the coverage report aggregating coverage from the unit
    test runs (not system test runs), and then erases coverage data.
    """
    session.install("coverage", "pytest-cov")
    session.run("coverage", "report", "--show-missing", "--fail-under=100")

    session.run("coverage", "erase")


@nox.session(name="old-emulator-system", python=ALL_INTERPRETERS)
def old_emulator_system(session):
    emulator_args = ["gcloud", "beta", "emulators", "datastore", "start"]
    _run_emulator(session, emulator_args)


@nox.session(name="emulator-system", python=ALL_INTERPRETERS)
def emulator_system(session):
    emulator_args = [
        "gcloud",
        "emulators",
        "firestore",
        "start",
        "--database-mode=datastore-mode",
    ]
    _run_emulator(session, emulator_args)


def _run_emulator(session, emulator_args):
    """Run the system test suite."""
    # Only run the emulator tests manually.
    if not session.interactive:
        return

    # TODO: It would be better to allow the emulator to bind to any port and pull
    # the port from stderr.
    emulator_args.append("--host-port=localhost:8092")
    emulator = subprocess.Popen(emulator_args, stderr=subprocess.PIPE)

    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )
    system_test_folder_path = os.path.join("tests", "system")

    # Install all test dependencies, then install this package into the
    # virtualenv's dist-packages.
    session.install("pytest")
    session.install("google-cloud-testutils")
    for local_dep in LOCAL_DEPS:
        session.install(local_dep)
    session.install(".", "-c", constraints_path)

    # Run py.test against the system tests.
    session.run(
        "py.test",
        "--quiet",
        system_test_folder_path,
        *session.posargs,
        env={"DATASTORE_EMULATOR_HOST": "localhost:8092"},
    )
    session.run("curl", "-d", "", "localhost:8092/shutdown", external=True)
    emulator.terminate()
    emulator.wait(timeout=2)


def run_black(session, use_check=False):
    args = ["black"]
    if use_check:
        args.append("--check")

    args.extend(
        [
            get_path("docs"),
            get_path("noxfile.py"),
            get_path("google"),
            get_path("tests"),
        ]
    )

    session.run(*args)


@nox.session(py=DEFAULT_INTERPRETER)
def lint(session):
    """Run linters.
    Returns a failure if the linters find linting errors or sufficiently
    serious code quality issues.
    """
    session.install("flake8", BLACK_VERSION, "click<8.1.0")
    run_black(session, use_check=True)
    session.run("flake8", "google", "tests")


@nox.session(py=DEFAULT_INTERPRETER)
def blacken(session):
    # Install all dependencies.
    session.install(BLACK_VERSION, "click<8.1.0")
    # Run ``black``.
    run_black(session)


@nox.session(py="3.10")
def docs(session):
    """Build the docs for this library."""

    session.install(".")
    session.install(
        # We need to pin to specific versions of the `sphinxcontrib-*` packages
        # which still support sphinx 4.x.
        # See https://github.com/googleapis/sphinx-docfx-yaml/issues/344
        # and https://github.com/googleapis/sphinx-docfx-yaml/issues/345.
        "sphinxcontrib-applehelp==1.0.4",
        "sphinxcontrib-devhelp==1.0.2",
        "sphinxcontrib-htmlhelp==2.0.1",
        "sphinxcontrib-qthelp==1.0.3",
        "sphinxcontrib-serializinghtml==1.1.5",
        "sphinx==4.5.0",
        "alabaster",
        "recommonmark",
    )

    shutil.rmtree(os.path.join("docs", "_build"), ignore_errors=True)
    session.run(
        "sphinx-build",
        "-W",  # warnings as errors
        "-T",  # show full traceback on exception
        "-N",  # no colors
        "-b",
        "html",
        "-d",
        os.path.join("docs", "_build", "doctrees", ""),
        os.path.join("docs", ""),
        os.path.join("docs", "_build", "html", ""),
    )


@nox.session(py="3.9")
def doctest(session):
    # Install all dependencies.
    session.install(
        # We need to pin to specific versions of the `sphinxcontrib-*` packages
        # which still support sphinx 4.x.
        # See https://github.com/googleapis/sphinx-docfx-yaml/issues/344
        # and https://github.com/googleapis/sphinx-docfx-yaml/issues/345.
        "sphinxcontrib-applehelp==1.0.4",
        "sphinxcontrib-devhelp==1.0.2",
        "sphinxcontrib-htmlhelp==2.0.1",
        "sphinxcontrib-qthelp==1.0.3",
        "sphinxcontrib-serializinghtml==1.1.5",
        "sphinx==4.0.1",
    )
    session.install(".")
    # Run the script for building docs and running doctests.
    run_args = [
        "sphinx-build",
        "-W",
        "-b",
        "doctest",
        "-d",
        get_path("docs", "_build", "doctrees"),
        get_path("docs"),
        get_path("docs", "_build", "doctest"),
    ]
    session.run(*run_args)


# Run the system tests
@nox.session(py=DEFAULT_INTERPRETER)
def system(session):
    """Run the system test suite."""
    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )
    system_test_path = get_path("tests", "system.py")
    system_test_folder_path = os.path.join("tests", "system")

    # Sanity check: Only run tests if the environment variable is set.
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""):
        session.skip("Credentials must be set via environment variable")

    system_test_exists = os.path.exists(system_test_path)
    system_test_folder_exists = os.path.exists(system_test_folder_path)
    # Sanity check: only run tests if found.
    if not system_test_exists and not system_test_folder_exists:
        session.skip("System tests were not found")

    # Use pre-release gRPC for system tests.
    # Exclude version 1.52.0rc1 which has a known issue.
    # See https://github.com/grpc/grpc/issues/32163.
    session.install("--pre", "grpcio!=1.52.0rc1")

    # Install all test dependencies, then install this package into the
    # virtualenv's dist-packages.
    session.install("pytest")
    session.install("google-cloud-testutils")
    for local_dep in LOCAL_DEPS:
        session.install(local_dep)
    session.install(".", "-c", constraints_path)

    # Run py.test against the system tests.
    if system_test_exists:
        session.run("py.test", "--quiet", system_test_path, *session.posargs)
    if system_test_folder_exists:
        session.run("py.test", "--quiet", system_test_folder_path, *session.posargs)
