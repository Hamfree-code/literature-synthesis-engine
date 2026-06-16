# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Hams & Co. Research Division desktop app (Windows, Flask + web UI)."""

block_cipher = None

a = Analysis(
    ['app_server.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Prompts
        ('config/prompts/triage_haiku.txt', 'config/prompts'),
        ('config/prompts/extraction_sonnet.txt', 'config/prompts'),
        ('config/prompts/synthesis_sonnet.txt', 'config/prompts'),
        ('config/prompts/due_diligence_sonnet.txt', 'config/prompts'),
        ('config/prompts/executive_summary_sonnet.txt', 'config/prompts'),
        ('config/prompts/heterogeneity_analysis.txt', 'config/prompts'),
        ('config/prompts/contradiction_check.txt', 'config/prompts'),
        ('config/prompts/arbiter_sonnet.txt', 'config/prompts'),
        # v3 SQL migrations (optional — for reference from inside the bundle)
        ('config/schema.sql', 'config'),
        ('config/schema_v2_migration.sql', 'config'),
        ('config/schema_v3_migration.sql', 'config'),
        ('config/schema_v3_2_migration.sql', 'config'),
        # v3.2 outcome dictionaries (WP-5/6 — consumed during normalisation)
        ('config/outcome_dictionary/long_covid.json', 'config/outcome_dictionary'),
        # Templates
        ('templates/report.md.j2', 'templates'),
        ('templates/due_diligence.md.j2', 'templates'),
        ('templates/executive_summary.md.j2', 'templates'),
        ('templates/app.html', 'templates'),
    ],
    hiddenimports=[
        # Flask stack
        'flask', 'werkzeug', 'werkzeug.serving', 'werkzeug.routing',
        'jinja2', 'click', 'itsdangerous', 'markupsafe', 'blinker',
        # Core LLM + HTTP
        'anthropic', 'httpx', 'h2', 'h11', 'hpack', 'hyperframe',
        'tenacity', 'pydantic', 'pydantic_settings', 'pydantic_core',
        # Data
        'supabase', 'postgrest', 'realtime', 'gotrue', 'storage3', 'supafunc',
        'Bio', 'Bio.Entrez',
        'lxml._elementpath', 'lxml.etree',
        # Reporting
        'rich', 'markdown', 'reportlab',
        'reportlab.pdfbase', 'reportlab.platypus', 'reportlab.lib.colors',
        # Numerics
        'sklearn', 'sklearn.cluster', 'numpy', 'pandas',
        # App modules
        'app_paths', 'app_pdf', 'bundled_credentials',
        'utils.run_context', 'utils.claude_client', 'utils.supabase_client',
        'utils.checkpointing', 'utils.logging_setup',
        'pipeline.phase1_ingest', 'pipeline.phase2_filter', 'pipeline.phase3_extract',
        'pipeline.phase4_store', 'pipeline.phase5_analyze', 'pipeline.phase6_report',
        'pipeline.runner',
        'utils.xml_parser', 'utils.umls_normalizer', 'utils.validation_engine',
        # v3.2 methodology engines (Methodological Hardening & Provenance Integrity)
        'methodology', 'methodology.emcu', 'methodology.extraction_schema',
        'methodology.flow_record', 'methodology.provenance_registry',
        'methodology.outcome_dictionary', 'methodology.rob_tools',
        'methodology.case_definition', 'methodology.grade_engine',
        'methodology.synthesis_gating', 'methodology.prevalence',
        'methodology.reconciliation', 'methodology.output_ceiling',
        'methodology.integration',
        # multiprocessing under PyInstaller spawn
        'multiprocessing', 'multiprocessing.spawn', 'multiprocessing.context',
        'multiprocessing.queues', 'multiprocessing.connection',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['weasyprint', 'asreview', 'matplotlib', 'tornado', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LongCovidResearch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
