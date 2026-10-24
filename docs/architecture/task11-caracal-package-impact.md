# Task 11 — Caracal package-impact review

**Assessment date:** 2026-10-24
**Status:** Package-resolution simulation complete; installation not approved or performed.

## Method and scope

The actual Ubuntu 22.04 host has no configured Ubuntu Cloud Archive source. Its existing apt cache therefore offered Jammy archive packages (including Yoga-era OpenStack packages), not Caracal. To assess the proposed Ubuntu 2024.1 Caracal path without changing host apt configuration, signed Ubuntu Jammy and Canonical `jammy-updates/caracal` indexes were fetched into a temporary directory under `/tmp`. The archive signing key was obtained from the Ubuntu-signed `ubuntu-cloud-keyring` package and used only from that temporary directory. No system source list, package, or service was changed.

The solver simulation used the actual host's dpkg status database, the temporary signed package indexes, `--no-install-recommends`, and this minimum requested package set:

```text
keystone glance placement-api nova-api nova-conductor nova-scheduler nova-compute
neutron-server neutron-plugin-ml2 neutron-ovn-metadata-agent python3-openstackclient
mariadb-server rabbitmq-server apache2 libapache2-mod-wsgi-py3
openvswitch-switch ovn-central ovn-host
```

This models a single-node Nova/Neutron-with-OVN POC without Cinder, Swift, Horizon, Heat, or other optional services. `apt-get -s` does not install or configure packages and does not prove that service startup or the deployment will succeed.

## Solver result

The signed Caracal simulation returned **11 upgrades, 228 new packages, 0 removals, and 22 packages not upgraded**. No broad system upgrade was simulated. The full resolver output, including the selected versions and repositories for all `Inst`/`Conf` entries, is preserved in [`task11-caracal-apt-simulation.txt`](task11-caracal-apt-simulation.txt). The simulation does not resolve any interactive conffile prompt or establish maintainer-script service restart behavior, disk-space use, or runtime resource use.

The exact existing-package upgrades in the simulated transaction were:

| Package | Installed | Caracal candidate |
|---|---:|---:|
| `python3-jsonschema` | 3.2.0-0ubuntu2 | 4.10.3-2ubuntu1~cloud0 |
| `python3-blinker` | 1.4+dfsg1-0.4 | 1.7.0-1~cloud0 |
| `python3-click` | 8.0.3-1 | 8.1.6-2~cloud0 |
| `libjs-sphinxdoc` | 4.3.2-1 | 7.2.6-6~cloud0 |
| `python3-openvswitch` | 2.17.12-0ubuntu0.22.04.1 | 3.3.9-0ubuntu0.24.04.1~cloud0 |
| `openvswitch-switch` | 2.17.12-0ubuntu0.22.04.1 | 3.3.9-0ubuntu0.24.04.1~cloud0 |
| `openvswitch-common` | 2.17.12-0ubuntu0.22.04.1 | 3.3.9-0ubuntu0.24.04.1~cloud0 |
| `ovn-controller-vtep` | 22.03.8-0ubuntu0.22.04.1 | 24.03.6-0ubuntu0.24.04.1~cloud0 |
| `ovn-host` | 22.03.8-0ubuntu0.22.04.1 | 24.03.6-0ubuntu0.24.04.1~cloud0 |
| `ovn-central` | 22.03.8-0ubuntu0.22.04.1 | 24.03.6-0ubuntu0.24.04.1~cloud0 |
| `ovn-common` | 22.03.8-0ubuntu0.22.04.1 | 24.03.6-0ubuntu0.24.04.1~cloud0 |

New direct services include Keystone, Glance, Placement, Nova API/conductor/scheduler/compute, Neutron server/ML2/OVN metadata agent, MariaDB, and RabbitMQ. `nova-compute-kvm` and `nova-compute-libvirt` are added. Apache and `libapache2-mod-wsgi-py3` were already at their newest candidates and are not new installs in this plan. Memcached is already installed. The simulation does not upgrade the installed libvirt 8.0.0 or QEMU 6.2 packages.

The solver also reports ten installed packages as “no longer required” (`apport-symptoms`, `htop`, `libevent-pthreads-2.1-7`, `libmecab2`, `libprotobuf-lite23`, `mecab-ipadic`, `mecab-ipadic-utf8`, `mecab-utils`, `python3-apport`, and `python3-problem-report`). It does **not** remove them in this transaction. Do not run `apt autoremove` as part of the POC work.

The complete set of **228 new package names** returned by this solver run is recorded below (the exact resolver-selected versions are in the temporary apt plan used for this assessment; package names are listed here to keep the repository report readable):

```text
mysql-common mariadb-common galera-4 libdbi-perl libconfig-inifiles-perl libmariadb3 mariadb-client-core-10.6 mariadb-client-10.6 mariadb-server-core-10.6 mariadb-server-10.6 nova-common python3-importlib-resources python3-mako python3-greenlet python3-sqlalchemy python3-typing-extensions python3-alembic python3-autopage python3-pyperclip python3-wcwidth python3-cmd2 sgml-base xml-core docutils-common python3-roman python3-docutils python3-pbr python3-prettytable python3-stevedore python3-cliff python3-iso8601 python3-lxml python3-os-service-types python3-keystoneauth1 python3-oslo.i18n python3-wrapt python3-debtcollector python3-msgpack ieee-data python3-netaddr python3-packaging python3-oslo.utils python3-oslo.serialization python3-barbicanclient python3-dateutil python3-dnspython python3-monotonic python3-eventlet python3-oslo.context python3-pyinotify python3-oslo.log python3-rfc3986 python3-oslo.config python3-castellan python3-simplejson python3-cinderclient python3-cursive python3-decorator python3-futurist python3-warlock python3-glanceclient keystone-common python3-dogpile.cache python3-extras python3-testtools python3-fixtures python3-itsdangerous python3-werkzeug python3-flask python3-aniso8601 python3-flask-restful python3-keystoneclient python3-memcache python3-oslo.cache pycadf-common python3-pycadf python3-webob python3-keystonemiddleware python3-ldap python3-sqlparse python3-tempita python3-migrate alembic python3-unittest2 python3-testresources python3-testscenarios python3-oslo.db python3-vine python3-amqp python3-cachetools python3-anyjson python3-kombu python3-prometheus-client python3-oslo.metrics python3-statsd python3-oslo.middleware python3-fasteners python3-oslo.concurrency python3-paste python-pastedeploy-tpl python3-pastedeploy python3-repoze.lru python3-routes python3-oslo.service python3-tenacity python3-oslo.messaging python3-oslo.policy python3-oslo.upgradecheck python3-osprofiler python3-passlib python3-pymysql python3-elementpath python3-xmlschema libxmlsec1 libxmlsec1-openssl xmlsec1 python3-defusedxml python3-pysaml2 python3-scrypt python3-keystone python3-microversion-parse python3-deprecation python3-jmespath python3-munch python3-platformdirs python3-requestsexceptions python3-openstacksdk python3-appdirs python3-os-client-config python3-osc-lib python3-neutronclient nvme-cli os-brick-common python3-os-win python3-ply python3-pycparser python3-cffi python3-oslo.privsep python3-retrying python3-os-brick python3-os-resource-classes python3-os-traits python3-oslo.versionedobjects libbpf1 libxdp1 python3-ovsdbapp python3-psutil python3-pyroute2 python3-os-vif python3-oslo.limit python3-oslo.reports python3-oslo.rootwrap python3-ddt python3-suds python3-oslo.vmware python3-nacl python3-paramiko python3-pymemcache python3-voluptuous python3-tooz python3-numpy python3-websockify zvmcloudconnector-common python3-zvmcloudconnector python3-nova nova-api bridge-utils nova-compute-libvirt nova-compute-kvm nova-compute nova-conductor nova-scheduler erlang-base erlang-asn1 erlang-crypto erlang-public-key erlang-mnesia erlang-runtime-tools erlang-ssl erlang-eldap erlang-ftp erlang-tftp erlang-inets erlang-snmp erlang-os-mon erlang-parsetools erlang-syntax-tools erlang-tools erlang-xmerl mariadb-server net-tools neutron-common python3-designateclient python3-tinyrpc python3-os-ken python3-logutils python3-simplegeneric python3-singledispatch python3-pecan python3-setproctitle python3-neutron-lib python3-novaclient python3-neutron neutron-ovn-metadata-agent neutron-plugin-ml2 neutron-server placement-common python3-placement placement-api python3-arrow python3-automaton python3-swiftclient python3-glance-store python3-networkx python3-osc-placement python3-openstackclient python3-psycopg2 python3-pyasyncore python3-semantic-version python3-sqlalchemy-utils python3-taskflow python3-wsme glance-common python3-glance glance-api glance keystone rabbitmq-server
```

## Existing-system impacts and conflicts

1. **OVS/OVN major upgrade:** the plan upgrades active OVS from 2.17 to 3.3 and OVN from 22.03 to 24.03. This is a major version transition affecting `openvswitch-switch`, OVSDB, `ovn-central`, `ovn-controller`, and the existing integration bridge/database state. Package simulation does not establish database migration or service restart behavior.
2. **VTEP package:** `ovn-controller-vtep` is upgraded despite the service being intentionally stopped and no VTEP use having been established. The simulation does not show whether maintainer scripts preserve the stopped state; verify after any separately approved installation.
3. **Modified OVN logrotate conffile:** `ovn-common` is upgraded and owns `/etc/logrotate.d/ovn-common`, which now contains the local timeout change. The simulation cannot determine a conffile prompt or operator choice. Preserve the approved local version and a separate backup before installation.
4. **libvirt/QEMU:** neither package is upgraded by this plan. Nova compute adds KVM/libvirt integration packages, but that does not validate Nova's behavior against the running libvirt daemon.
5. **Databases and brokers:** MariaDB and RabbitMQ are new packages/services. Existing state and service names must be checked before configuring them; no existing database was found in the earlier inventory.
6. **No package removals:** the solver planned zero removals. This does not rule out service restarts, changes to OVS/OVN database schemas, or configuration replacement.

## Rollback plan before any installation

1. Obtain separate approval for a pre-install snapshot/backup method. Preserve the Ubuntu 22.04 distribution, all VM disks, OVS/OVN databases, libvirt network definition, package configuration, and the locally edited logrotate conffile. Do not overwrite existing backups.
2. Record the exact installed package versions and enabled/active states, and save a verified copy of `/etc/apt` source configuration. Keep the timer and VTEP service stopped unless separately approved.
3. Back up OVS and OVN databases consistently using their supported online backup procedure, plus `/etc/openvswitch`, `/etc/ovn`, and `/etc/logrotate.d/ovn-common`. A plain copy of live database files is not accepted as a verified backup.
4. If deployment fails, do not run autoremove or broad package removal. Stop only task-installed OpenStack services after approval; restore the saved databases and configuration; then use an explicit, reviewed downgrade plan for the recorded OVS/OVN and Python package versions. Verify the original OVS/OVN topology and libvirt network before any service is resumed.
5. If database or package downgrade cannot be proven safe, use only a separately approved recovery from the verified pre-install snapshot. Package removal alone is not a rollback because OVS/OVN database migrations and service state may persist.

**Installation gate:** this review does not authorize enabling UCA on the host, installing packages, upgrading OVS/OVN, configuring services, starting VTEP, or changing the logrotate timer. The exact package transaction and rollback snapshot require user review and approval first.
