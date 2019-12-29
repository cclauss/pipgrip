import logging
from typing import Any, Dict, Hashable, List, Optional

import pkg_resources

from pipgrip.libs.mixology.constraint import Constraint
from pipgrip.libs.mixology.package_source import PackageSource as BasePackageSource
from pipgrip.libs.mixology.range import Range
from pipgrip.libs.mixology.union import Union
from pipgrip.libs.semver import Version, VersionRange, parse_constraint
from pipgrip.pipper import discover_dependencies_and_versions

logger = logging.getLogger(__name__)


class Dependency:
    def __init__(self, name, constraint):  # type: (str, str) -> None
        self.name = name
        self.constraint = parse_constraint(constraint or "*")
        self.pretty_constraint = constraint

    def __str__(self):  # type: () -> str
        return self.pretty_constraint


class PackageSource(BasePackageSource):
    """
    Provides information about specifications and dependencies to the resolver,
    allowing the VersionResolver class to remain generic while still providing power
    and flexibility.

    This contract contains the methods that users of Mixology must implement
    using knowledge of their own model classes.

    Note that the following concepts needs to be implemented
    in order to make the resolver work as best as possible:


    ## Package

    This user-defined class will be used to represent
    the various packages being resolved.

    __str__() will be called when providing information and feedback,
    in most cases it should return the name of the package.

    It also should implement __eq__ and __hash__.


    ## Version

    This user-defined class will be used to represent a single version.

    Versions of the same package will be compared to each other, however
    they do not need to store their associated package.

    As such they should be comparable. __str__() should also preferably be defined.


    ## Dependency

    This user-defined class represents a requirement of a package to another.

    It is returned by dependencies_for(package, version) and will be passed to
    convert_dependency(dependency) to convert it to a format Mixology understands.

    __eq__() should be defined.
    """

    def __init__(
        self, cache_dir, index_url, extra_index_url,
    ):  # type: () -> None
        self._root_version = Version.parse("0.0.0")
        self._root_dependencies = []
        self._packages = {}
        self.cache_dir = cache_dir
        self.index_url = index_url
        self.extra_index_url = extra_index_url

        super(PackageSource, self).__init__()

    @property
    def root_version(self):
        return self._root_version

    def add(
        self, name, version, deps=None
    ):  # type: (str, str, Optional[Dict[str, str]]) -> None
        version = Version.parse(version)
        if name not in self._packages:
            self._packages[name] = {}

        # if already added without deps, assume now called with discovered deps and overwrite
        if version in self._packages[name] and not (
            deps is None or self._packages[name][version] is None
        ):
            raise ValueError("{} ({}) already exists".format(name, version))

        # not existing and deps undiscovered
        if deps is None:
            self._packages[name][version] = None
            return

        dependencies = []
        for dep in deps:
            req = pkg_resources.Requirement.parse(dep)
            constraint = ",".join(["".join(tup) for tup in req.specs])
            dependencies.append(Dependency(req.key, constraint))

        self._packages[name][version] = dependencies

    def discover_and_add(self, package):  # type: (str, str) -> None
        # converting from semver constraint to pkg_resources string
        req = pkg_resources.Requirement.parse(package)
        to_create = discover_dependencies_and_versions(
            package, self.index_url, self.extra_index_url, self.cache_dir
        )
        for version in to_create["available"]:
            self.add(req.key, version)
        self.add(
            req.key, to_create["version"], deps=to_create["requires"],
        )

    def root_dep(self, package):  # type: (str, str) -> None
        req = pkg_resources.Requirement.parse(package)
        constraint = ",".join(["".join(tup) for tup in req.specs])
        self._root_dependencies.append(Dependency(req.key, constraint))
        self.discover_and_add(req.__str__())

    def _versions_for(
        self, package, constraint=None
    ):  # type: (Hashable, Any) -> List[Hashable]
        """
        Search for the specifications that match the given constraint.

        Called by BasePackageSource.versions_for
        """

        if package not in self._packages:
            self.discover_and_add(
                package + str(constraint).replace("||", "|").replace(" ", "")
            )
        if package not in self._packages:
            return []

        versions = []
        for version in self._packages[package].keys():
            if not constraint or constraint.allows_any(
                Range(version, version, True, True)
            ):
                versions.append(version)

        return sorted(versions, reverse=True)

    def dependencies_for(self, package, version):  # type: (Hashable, Any) -> List[Any]
        if package == self.root:
            return self._root_dependencies

        if self._packages[package][version] is None:
            # populate dependencies for version
            self.discover_and_add(package + "==" + str(version))
        return self._packages[package][version]

    def convert_dependency(self, dependency):  # type: (Dependency) -> Constraint
        """
        Converts a user-defined dependency (returned by dependencies_for())
        into a format Mixology understands.
        """
        if isinstance(dependency.constraint, VersionRange):
            constraint = Range(
                dependency.constraint.min,
                dependency.constraint.max,
                dependency.constraint.include_min,
                dependency.constraint.include_max,
                dependency.pretty_constraint,
            )
        else:
            # VersionUnion
            ranges = [
                Range(
                    range.min,
                    range.max,
                    range.include_min,
                    range.include_max,
                    str(range),
                )
                for range in dependency.constraint.ranges
            ]
            constraint = Union.of(*ranges)

        return Constraint(dependency.name, constraint)