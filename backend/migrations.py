from sqlalchemy import inspect, text

def upgrade_seven(c):
    inspector=inspect(c)
    additions={'devices':{'device_kind':"VARCHAR(16) NOT NULL DEFAULT 'unknown'"},
      'workflows':{'execution_mode':"VARCHAR(16) NOT NULL DEFAULT 'sequential'",'target_snapshot':'JSON','conversation_id':'VARCHAR(64)'}}
    for table,columns in additions.items():
        known={x['name'] for x in inspector.get_columns(table)}
        for name,ddl in columns.items():
            if name not in known:c.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    c.execute(text("CREATE TABLE IF NOT EXISTS conversations (id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(64) NOT NULL, title VARCHAR(80) NOT NULL, created FLOAT NOT NULL, updated FLOAT NOT NULL, deleted BOOLEAN NOT NULL, target_scope JSON NOT NULL)"))
    c.execute(text("CREATE TABLE IF NOT EXISTS conversation_turns (id VARCHAR(64) PRIMARY KEY, conversation_id VARCHAR(64) NOT NULL, request_hash VARCHAR(64) NOT NULL, state VARCHAR(32) NOT NULL, created FLOAT NOT NULL, workflow_id VARCHAR(64), source VARCHAR(32) NOT NULL, summary JSON)"))
    c.execute(text("CREATE TABLE IF NOT EXISTS workflow_steps (workflow_id VARCHAR(64) NOT NULL,ordinal INTEGER NOT NULL,task_id VARCHAR(64) UNIQUE,verification_hash VARCHAR(64),PRIMARY KEY (workflow_id,ordinal))"))
    c.execute(text("UPDATE schema_version SET id=7"))
    upgrade_eight(c)
    upgrade_nine(c)

def schema_contract(revision):
    from .store import metadata
    removed=set();omitted={}
    if revision<9:
        removed|={'schedules','schedule_runs'};omitted['tasks']={'schedule_run_id'}
    if revision<8:
        removed.add('task_recoveries');omitted.setdefault('devices',set()).add('agent_protocol');omitted.setdefault('tasks',set()).add('recovery_of')
    if revision<7:
        removed|={'conversations','conversation_turns'};omitted.setdefault('devices',set()).add('device_kind')
        omitted['workflows']={'execution_mode','target_snapshot','conversation_id'}
    if revision<6:
        removed.add('accounts');omitted['enrollment']={'profile'}
    return set(metadata.tables)-removed,omitted

def schema_shape(c,revision=9,legacy_seven=False):
    from .store import metadata
    if legacy_seven:revision=7
    inspector=inspect(c);expected_tables,omitted=schema_contract(revision)
    if set(inspector.get_table_names())!=expected_tables:raise ValueError('incomplete_schema')
    for name in expected_tables:
        if {x['name'] for x in inspector.get_columns(name)}!={x.name for x in metadata.tables[name].columns}-omitted.get(name,set()):raise ValueError('incomplete_schema')

def upgrade_nine(c):
    from .store import schedules,schedule_runs
    schema_shape(c,revision=8)
    c.execute(text("ALTER TABLE tasks ADD COLUMN schedule_run_id VARCHAR(64)"))
    schedules.create(c);schedule_runs.create(c)
    c.execute(text("UPDATE schema_version SET id=9"))
    schema_shape(c,revision=9)

def upgrade_eight(c):
    schema_shape(c,legacy_seven=True)
    c.execute(text("ALTER TABLE devices ADD COLUMN agent_protocol INTEGER NOT NULL DEFAULT 0"))
    c.execute(text("ALTER TABLE tasks ADD COLUMN recovery_of VARCHAR(64)"))
    c.execute(text("CREATE TABLE task_recoveries (original_task_id VARCHAR(64) PRIMARY KEY,device_id VARCHAR(64) NOT NULL,owner_id VARCHAR(64) NOT NULL,original_plan_hash VARCHAR(64) NOT NULL,program_hash VARCHAR(64) NOT NULL,program JSON NOT NULL,verification_hash VARCHAR(64),receipt JSON,latest_recovery_id VARCHAR(64),historical BOOLEAN NOT NULL DEFAULT FALSE)"))
    c.execute(text("UPDATE schema_version SET id=8"))
    schema_shape(c,revision=8)


def migrate(engine):
    inspector=inspect(engine)
    if not inspector.has_table("schema_version"):return
    with engine.begin() as c:
        versions=list(c.execute(text("SELECT id FROM schema_version")).scalars())
        if len(versions)!=1 or versions[0] not in (1,2,3,4,5,6,7,8,9):raise ValueError("unsupported_schema_version")
        revision=versions[0]
        if revision==9:
            schema_shape(c,revision=9)
            return
        if revision==8:
            upgrade_nine(c)
            return
        if revision==7:
            upgrade_eight(c)
            upgrade_nine(c)
            return
        dcols={x['name'] for x in inspector.get_columns('devices')};tcols={x['name'] for x in inspector.get_columns('tasks')}
        if revision>=2 and ('installations_enabled' not in dcols or 'package_meta' not in tcols):raise ValueError("incomplete_schema")
        if revision>=3 and ('diagnostics_enabled' not in dcols or not inspector.has_table('investigation_jobs')):raise ValueError("incomplete_schema")
        if revision>=4:
            if 'owner_id' not in tcols or not inspector.has_table('workflows') or 'owner_id' not in {x['name'] for x in inspector.get_columns('workflows')} or 'actor_id' not in {x['name'] for x in inspector.get_columns('events')}:raise ValueError("incomplete_schema")
            if revision>=5:
                if not inspector.has_table("knowledge_records"):raise ValueError("incomplete_schema")
                if revision==6:
                    if not inspector.has_table("accounts") or 'profile' not in {x['name'] for x in inspector.get_columns('enrollment')}:raise ValueError('incomplete_schema')
                    upgrade_seven(c)
                    return
        if 'installations_enabled' not in dcols:c.execute(text("ALTER TABLE devices ADD COLUMN installations_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
        if 'package_meta' not in tcols:c.execute(text("ALTER TABLE tasks ADD COLUMN package_meta JSON"))
        if 'diagnostics_enabled' not in dcols:c.execute(text("ALTER TABLE devices ADD COLUMN diagnostics_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
        c.execute(text("CREATE TABLE IF NOT EXISTS investigation_jobs (task_id VARCHAR(64) PRIMARY KEY, scope JSON, scope_hash VARCHAR(64), analysis_state VARCHAR(40), analysis_meta JSON, created FLOAT)"))
        if 'owner_id' not in tcols:c.execute(text("ALTER TABLE tasks ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT 'local-development'"))
        if not inspector.has_table('workflows'):
            c.execute(text("CREATE TABLE workflows (id VARCHAR(64) PRIMARY KEY, session_id VARCHAR(64), request_hash VARCHAR(64), state VARCHAR(40), source VARCHAR(32), plan_hash VARCHAR(64), created FLOAT, expires FLOAT, decision BOOLEAN, owner_id VARCHAR(64) NOT NULL DEFAULT 'local-development')"))
        elif 'owner_id' not in {x['name'] for x in inspector.get_columns('workflows')}:
            c.execute(text("ALTER TABLE workflows ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT 'local-development'"))
        if 'actor_id' not in {x['name'] for x in inspector.get_columns('events')}:
            c.execute(text("ALTER TABLE events ADD COLUMN actor_id VARCHAR(64) NOT NULL DEFAULT 'local-development'"))
        c.execute(text("CREATE TABLE IF NOT EXISTS knowledge_records (id VARCHAR(64) PRIMARY KEY, source_id VARCHAR(32), source_version INTEGER, space VARCHAR(64), os_family VARCHAR(16), service VARCHAR(64), applies_version VARCHAR(64), task_id VARCHAR(64), owner_id VARCHAR(64), reviewer_id VARCHAR(64), state VARCHAR(24), revision INTEGER, updated FLOAT)"))
        c.execute(text("CREATE TABLE IF NOT EXISTS accounts (id VARCHAR(64) PRIMARY KEY, username VARCHAR(64) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL, roles JSON NOT NULL, device_ids JSON NOT NULL, knowledge_spaces JSON NOT NULL, disabled BOOLEAN NOT NULL, auth_version INTEGER NOT NULL, created FLOAT NOT NULL)"))
        if 'profile' not in {x['name'] for x in inspector.get_columns('enrollment')}:c.execute(text('ALTER TABLE enrollment ADD COLUMN profile JSON'))
        c.execute(text("UPDATE schema_version SET id=6"))
        upgrade_seven(c)
