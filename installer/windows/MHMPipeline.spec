# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MHM Pipeline (Windows one-folder build).
#
# Run from the repo root on a Windows host:
#   pyinstaller installer\windows\MHMPipeline.spec --noconfirm
#
# Produces: dist\MHMPipeline\MHMPipeline.exe + dist\MHMPipeline\_internal\...
# Inno Setup (build_installer.iss) then wraps that folder into a single .exe.

from pathlib import Path

# PyInstaller injects these names; importing keeps editors happy.
try:
    from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT  # noqa: F401
except ImportError:  # pragma: no cover - only true outside PyInstaller
    pass

from PyInstaller.utils.hooks import copy_metadata

# ---------------------------------------------------------------------------
# Bundled data — mirrors installer/macos/build_app.sh asset list.
# Each tuple is (source_path_relative_to_repo_root, dest_path_inside_bundle).
# Optional files are guarded with Path.exists() so a missing classifier
# checkpoint doesn't break the build.
# ---------------------------------------------------------------------------

_REPO = Path(SPECPATH).resolve().parent.parent


def _opt(src: str, dst: str) -> list:
    """Return [(absolute_src, dst)] if the file exists, else []."""
    p = _REPO / src
    return [(str(p), dst)] if p.exists() else []


def _opt_dir(src: str, dst: str) -> list:
    """Return [(absolute_src, dst)] if the directory exists and is non-empty, else []."""
    p = _REPO / src
    if p.is_dir() and any(p.iterdir()):
        return [(str(p), dst)]
    return []


datas = []

# Package distribution metadata (.dist-info/METADATA).
# Without these, `importlib.metadata.version("foo")` returns None inside the
# frozen bundle, which crashes transformers' compatibility checks at startup
# with "Unable to compare versions for huggingface-hub>=1.5.0,<2.0:
# need=1.5.0 found=None". Every package whose version is read at runtime by
# transformers, huggingface_hub, tokenizers, pyshacl, etc. needs its metadata
# bundled. Use copy_metadata() so PyInstaller resolves the .dist-info path
# against the build venv automatically.
for _pkg in (
    'transformers', 'huggingface_hub', 'tokenizers', 'safetensors',
    'torch', 'numpy', 'requests', 'tqdm', 'packaging', 'filelock',
    'PyYAML', 'regex', 'pyshacl', 'rdflib', 'pymarc', 'wikibaseintegrator',
):
    try:
        datas += copy_metadata(_pkg)
    except Exception:
        # Package not installed in the build venv — skip silently. The
        # runtime importer will only ask for metadata if the import path
        # is reachable, so a missing optional package is harmless.
        pass

# Authority databases (required for Stage 3).
datas += _opt('converter/authority/mazal_index.db', 'converter/authority')
datas += _opt('data/kima/kima_index.db', 'data/kima')

# NER model checkpoints (required for Stage 2 provenance + contents).
datas += _opt('ner/provenance_ner_model.pt', 'ner')
datas += _opt('ner/contents_ner_model.pt', 'ner')

# Optional classifier checkpoints.
datas += _opt('ner/genre_classifier_model.pt', 'ner')
datas += _opt('ner/marc500_classifier_model.pt', 'ner')

# HuggingFace snapshots (staged into models/ by package_for_windows_build.sh).
datas += _opt_dir('models/hebrew-manuscript-joint-ner-v2', 'models/hebrew-manuscript-joint-ner-v2')
datas += _opt_dir('models/dictabert', 'models/dictabert')

# Ontology + SHACL shapes (required for Stages 4 and 5).
datas += _opt('ontology/hebrew-manuscripts.ttl', 'ontology')
datas += _opt('ontology/shacl-shapes.ttl', 'ontology')

# Some code reads version metadata from pyproject.toml at runtime.
datas += _opt('pyproject.toml', '.')


# ---------------------------------------------------------------------------
# Hidden imports — modules PyInstaller's static analyser tends to miss.
# ---------------------------------------------------------------------------

hiddenimports = [
    # PyQt6 plugins / dynamically loaded modules.
    'PyQt6.sip',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtPrintSupport',
    'PyQt6.QtSvg',
    'PyQt6.QtNetwork',

    # Hugging Face Transformers — loaded dynamically by AutoModel/AutoTokenizer.
    'transformers',
    'transformers.models',
    'transformers.models.auto',
    'transformers.models.auto.modeling_auto',
    'transformers.models.auto.tokenization_auto',
    'transformers.models.auto.configuration_auto',
    'transformers.models.bert',
    'transformers.models.bert.modeling_bert',
    'transformers.models.bert.tokenization_bert',
    'transformers.models.bert.tokenization_bert_fast',
    'transformers.models.bert.configuration_bert',
    'tokenizers',
    'safetensors',
    'safetensors.torch',
    'huggingface_hub',

    # Torch — submodules sometimes missed.
    'torch',
    'torch.nn',
    'torch.nn.functional',

    # rdflib — parser/serializer plugins are entry-point loaded.
    'rdflib',
    'rdflib.plugins',
    'rdflib.plugins.parsers',
    'rdflib.plugins.parsers.notation3',
    'rdflib.plugins.parsers.ntriples',
    'rdflib.plugins.parsers.nquads',
    'rdflib.plugins.parsers.rdfxml',
    'rdflib.plugins.parsers.trig',
    'rdflib.plugins.parsers.trix',
    'rdflib.plugins.parsers.jsonld',
    'rdflib.plugins.serializers',
    'rdflib.plugins.serializers.turtle',
    'rdflib.plugins.serializers.nt',
    'rdflib.plugins.serializers.nquads',
    'rdflib.plugins.serializers.rdfxml',
    'rdflib.plugins.serializers.trig',
    'rdflib.plugins.serializers.jsonld',
    'rdflib.plugins.stores',
    'rdflib.plugins.stores.memory',
    'rdflib.plugins.sparql',
    'rdflib.plugins.sparql.parser',

    # SHACL.
    'pyshacl',
    'pyshacl.rules',
    'pyshacl.constraints',

    # Wikidata client.
    'wikibaseintegrator',
    'wikibaseintegrator.wbi_config',

    # Misc dynamic imports.
    'pymarc',
    'platformdirs',
    'requests',
    'urllib3',
    'certifi',
    'charset_normalizer',
    'idna',

    # ner/ is not a package (no __init__.py) — these are loose modules that
    # the inference pipeline imports without the `ner.` prefix when frozen.
    'postprocessing_rules',
    'entity_normalize',
    'inference_pipeline',
    'ner_inference_pipeline',
    'marc500_sentence_model',
    'genre_classifier_model',
    'train_ner_model_kfold',
]


# ---------------------------------------------------------------------------
# Excludes — keep the bundle small by skipping training-only data and tests.
# ---------------------------------------------------------------------------

excludes = [
    'tests',
    'paper',
    'data.tsvs',
    'data.NLI_AUTHORITY_XML',
    'data.mrc',
    'data.output',
    'data.samples',
    'ner.raw_data',
    'ner.processed_data',
    'ner.training_runs',
    'matplotlib',
    'IPython',
    'jupyter',
    'notebook',
    'pandas.tests',
    'numpy.tests',
    'scipy.tests',
]


# ---------------------------------------------------------------------------
# Build pipeline.
# ---------------------------------------------------------------------------

block_cipher = None


a = Analysis(
    [str(_REPO / 'src' / 'mhm_pipeline' / 'app.py')],
    pathex=[str(_REPO), str(_REPO / 'src'), str(_REPO / 'ner')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MHMPipeline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_REPO / 'installer' / 'windows' / 'mhm_pipeline.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MHMPipeline',
)
