
> Original local POC workflow and implementation traceability. The local POC remains the current development scope; this diagram preserves its task IDs and dependencies but does not automatically reactivate T01–T28 as a sequence. Local evidence feeds the separate [`../staging-release-plan.md`](../staging-release-plan.md); it does not itself pass staging or production acceptance. AWS-hosted OpenStack is deferred and outside current implementation scope.

```mermaid
flowchart TD
    START([Start: OpenStack management POC])

    subgraph PLAN["Plan and local foundation"]
        T01["T01: Baseline audit and planning docs"]
        T02["T02: Read-only capacity, Git, and service discovery"]
        T03{"T03: Select management-first,<br/>fixture-first route"}
        T04["T04: Repository and config scaffold"]
        T05["T05: Assess client/tool prerequisites"]
        T06["T06: Fixture identity and role policy"]
        T07["T07: OpenStack inventory<br/>Local observations now;<br/>remote checks need endpoint/scope approval"]
        T08["T08: Capability and catalog model"]
        T09["T09: Deterministic manager-independent fixture adapter"]
        T10["T10: Capability status reporting"]
        T11["T11: Project isolation verification"]
    end

    START --> T01 --> T02 --> T03
    T03 -->|"Selected route"| T04 --> T05 --> T06
    T03 --> T07
    T06 --> T08
    T06 --> T09
    T08 --> T09
    T07 --> T10
    T09 --> T10
    T06 --> T11

    subgraph MGMT["Fixture-backed management"]
        T12["T12: Provisioning contract<br/>Only if custom API need is demonstrated"]
        T13["T13: Synchronous fixture management service"]
        T14{"T14: Is asynchronous tracking<br/>needed and backed by an execution mechanism?"}
        JOB["T14 fixture tracker<br/>Simulated, in-memory, manually advanced;<br/>not connected to provisioning"]
    end

    T08 --> T12
    T09 --> T12
    T10 --> T12
    T12 --> T13
    T13 --> T14
    T14 -->|"No demonstrated async operation"| NOJOB["No queue or integrated job tracking<br/>T13 remains synchronous"]
    T14 -->|"Standalone model only"| JOB

    subgraph INFRA["Conditional OpenStack workflows"]
        T15{"T15: Neutron network path<br/>confirmed and approved?"}
        NETOK["Evidence suitable test path<br/>or document unavailable"]
        T16GATE{"T16 gate: Nova endpoint or approved deployment;<br/>network, image, flavor, quota, cost, cleanup approval;<br/>sync/async and T14 dependency decision"}
        T16["T16: Nova create, inspect,<br/>start, stop, delete"]
        T17["T17: Repeatable compute/network acceptance"]
        T18["T18: Cinder backend-gated storage"]
        T19["T19: Optional object storage"]
        T20["T20: Usage data source"]
        T21["T21: Usage reporting/demo pricing"]
        T22["T22: Optional repeatable infrastructure automation"]
    end

    T03 --> T15
    T07 --> T15
    T15 -->|"Approved and supported"| NETOK --> T16GATE
    T15 -->|"Unavailable or not approved"| NETMISS["Record unavailable / pending;<br/>do not infer a usable path"]
    T07 --> T16GATE
    T09 --> T16GATE
    T14 --> T16GATE
    T16GATE -->|"Gates resolved"| T16 --> T17
    T16GATE -->|"Unresolved"| T16BLOCK["T16 remains blocked"]
    T07 --> T18
    T16 --> T18
    T07 --> T19
    T10 --> T20 --> T21
    T17 --> T22

    subgraph CLOSE["Security, operations, and final acceptance"]
        T23["T23: GPU feasibility"]
        T24["T24: Optional sample AI workload"]
        T25["T25: Security validation"]
        T26["T26: Optional logging/monitoring"]
        T27["T27: Backup/restore"]
        T28{"T28: End-to-end demonstration<br/>and final acceptance"}
        PASS([Accepted with evidence,<br/>limitations, and task records])
        GAP["Report unsupported, simulated,<br/>deferred, or blocked items accurately"]
    end

    T02 --> T23 --> T24
    T11 --> T25
    T13 --> T25
    T15 --> T25
    T16 --> T25
    T14 --> T26
    T17 --> T26
    T18 --> T27
    T19 --> T27
    T20 --> T27
    T26 --> T27
    T10 --> T28
    T11 --> T28
    T14 --> T28
    T17 --> T28
    T18 --> T28
    T19 --> T28
    T20 --> T28
    T21 --> T28
    T23 --> T28
    T24 --> T28
    T25 --> T28
    T26 --> T28
    T27 --> T28
    T28 -->|"Evidence and required gates pass"| PASS
    T28 -->|"Capability unavailable or gate unresolved"| GAP


    CALLER["Caller supplies fixture identity<br/>and operation request"]

    AUTHN["Fixture identity provider<br/>Resolve identity reference"]
    AUTHNOK{"Known fixture identity?"}
    AUTHNFAIL["Return simulated authentication failure"]

    CALLER --> AUTHN --> AUTHNOK
    AUTHNOK -->|"No"| AUTHNFAIL
    AUTHNOK -->|"Yes"| ENTRY{"Choose service path"}

    subgraph ACCEPT["T13 synchronous management path"]
        REQ["compute.create payload:<br/>operation, project_id, image_id,<br/>flavor_id, network_id"]
        VALIDATE["Contract validation:<br/>exact fields, supported operation,<br/>nonempty single-line references"]
        VALID{"Valid?"}
        AUTHZ["Authorize requested project<br/>and compute.operator role"]
        AUTHZOK{"Authorized?"}
        LOOKUP["Check image, flavor, and network<br/>against fixture catalog"]
        FOUND{"All fixture references found?"}
        ACCEPTED["Return status=accepted,<br/>evidence_mode=simulated,<br/>source=fixture"]
        ACCEPTERR["Return safe validation,<br/>authorization, or fixture error"]
        NOCREATE["No resource ID;<br/>no job ID;<br/>no queue or resource creation"]

        REQ --> VALIDATE --> VALID
        VALID -->|"No"| ACCEPTERR
        VALID -->|"Yes"| AUTHZ --> AUTHZOK
        AUTHZOK -->|"No"| ACCEPTERR
        AUTHZOK -->|"Yes"| LOOKUP --> FOUND
        FOUND -->|"No"| ACCEPTERR
        FOUND -->|"Yes"| ACCEPTED --> NOCREATE
    end

    subgraph LIFECYCLE["Fixture instance lifecycle service"]
        CREATEPAYLOAD["Create payload:<br/>compute.create + project/image/flavor/network"]
        LVALIDATE["Validate request contract"]
        LVALID{"Valid?"}
        CREATEAUTHZ["Authorize compute.create<br/>for the requested project"]
        CREATEAUTHZOK{"Authorized?"}
        LCATALOG["Check image, flavor, and network<br/>against fixture catalog"]
        LFOUND{"References found?"}
        CREATE["In-memory fixture provider<br/>creates active InstanceRecord"]
        INSTANCEID["Return simulated fixture-instance ID<br/>and active state"]
        CREATEERR["Return safe lifecycle error:<br/>validation, authorization,<br/>missing fixture, timeout, or provider failure"]

        CREATEPAYLOAD --> LVALIDATE --> LVALID
        LVALID -->|"No"| CREATEERR
        LVALID -->|"Yes"| CREATEAUTHZ --> CREATEAUTHZOK
        CREATEAUTHZOK -->|"No"| CREATEERR
        CREATEAUTHZOK -->|"Yes"| LCATALOG --> LFOUND
        LFOUND -->|"No"| CREATEERR
        LFOUND -->|"Yes"| CREATE --> INSTANCEID

        OP{"Next lifecycle action"}
        INSTANCEID --> OP
        OP -->|"inspect"| INSPECTAUTH["Authorize compute.inspect"]
        OP -->|"start"| STARTAUTH["Authorize compute.start"]
        OP -->|"stop"| STOPAUTH["Authorize compute.stop"]
        OP -->|"delete"| DELETEAUTH["Authorize compute.delete"]

        INSPECTAUTH --> POLICY{"Operation has an allowed fixture role?"}
        STARTAUTH --> POLICY
        STOPAUTH --> POLICY
        DELETEAUTH --> POLICY

        POLICY -->|"No: current policy has no mapping"| DENIED["Return authorization_denied<br/>No state transition"]
        POLICY -->|"If policy is extended"| SCOPE["Check project scope and instance ID"]
        SCOPE --> SCOPEOK{"Authorized and matching<br/>task-created fixture instance?"}
        SCOPEOK -->|"No"| LIFEERR["Return safe not-found,<br/>not-task-created, or invalid-ID error"]
        SCOPEOK -->|"Yes"| TRANSITION{"Valid state transition?"}
        TRANSITION -->|"No"| TRANSERR["Return invalid_transition"]
        TRANSITION -->|"Yes: inspect"| REPORT["Return current simulated record"]
        TRANSITION -->|"Yes: start"| ACTIVE["State becomes active"]
        TRANSITION -->|"Yes: stop"| STOPPED["State becomes stopped"]
        TRANSITION -->|"Yes: delete"| DELETED["State becomes deleted<br/>(record remains in in-memory fixture store)"]
        ACTIVE --> OP
        STOPPED --> OP
        REPORT --> OP
        DELETED --> DONE([Fixture lifecycle flow ends])
    end

    ENTRY -->|"T13 accepted-only request"| REQ
    ENTRY -->|"T16 fixture lifecycle"| CREATEPAYLOAD

    subgraph NET["T15 fixture network policy evaluator"]
        NINPUT["Fixture topology, project,<br/>security group, direction,<br/>protocol, peer IP, port"]
        NAUTH["Authorize catalog.read"]
        NAUTHOK{"Authorized and same project?"}
        NVALID["Validate topology references,<br/>CIDRs, rules, and ports"]
        NMATCH{"Rule matches direction,<br/>protocol, port, and peer CIDR?"}
        ALLOW["Simulated allow decision"]
        DENY["Simulated default-deny decision"]
        NERROR["Safe validation or scope error"]

        NINPUT --> NAUTH --> NAUTHOK
        NAUTHOK -->|"No"| NERROR
        NAUTHOK -->|"Yes"| NVALID --> NMATCH
        NMATCH -->|"Yes"| ALLOW
        NMATCH -->|"No"| DENY
    end

    subgraph JOBS["T14 simulated job tracker"]
        JCREATE["Manually create fixture job"]
        JPENDING["pending"]
        JRUNNING["running"]
        JEND{"Outcome"}
        JSUCCESS["succeeded"]
        JFAILED["failed with allowlisted category"]
        JCREATE --> JPENDING --> JRUNNING --> JEND
        JPENDING --> JFAILED
        JEND -->|"Success"| JSUCCESS
        JEND -->|"Failure"| JFAILED
    end

    PROVISION["T13/T16 provisioning flow"]
    NOQUEUE["No queue, worker, persistence,<br/>or integrated job tracking currently"]
    PROVISION -. "not connected to T14" .-> NOQUEUE

    REAL{"Future real Nova route"}
    GATES["Requires verified endpoint or approved deployment plan,<br/>task-specific operation/resource/cleanup approval,<br/>usable approved Neutron path,<br/>and T16 execution/dependency decision"]
    NOVA["Real Nova operation<br/>behind manager-independent adapter"]
    REAL -->|"Gates pass"| GATES --> NOVA
    REAL -->|"Gates absent"| NOQUEUE

    ```
