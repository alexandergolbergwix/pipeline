# Registering the `/mhm/` prefix at w3id.org

This is the one-time setup to register `https://w3id.org/mhm/*` as the
permanent identifier namespace for the Mapping Hebrew Manuscripts (MHM)
project. After it merges, all Wikidata P2888 (exact match) URIs minted by
the pipeline will resolve through `w3id.org`, giving the project URIs
that survive any future hosting migration.

## Why w3id.org

`w3id.org` is the W3C Permanent Identifier Community Group's free,
community-maintained URL redirection service used by hundreds of
Linked Data projects (FOAF, schema.org overlays, FactGrid mappings,
CIDOC-CRM, etc.). The redirect rules are public, version-controlled on
GitHub, and survive any individual project's hosting choice.

## Prerequisites

- A GitHub account ([alexandergolbergwix](https://github.com/alexandergolbergwix)
  per the project's `origin` remote).
- ~20 minutes of active work + ~1–2 weeks of community review.

## Step-by-step

### 1. Fork the repository

Go to [github.com/perma-id/w3id.org](https://github.com/perma-id/w3id.org)
and click **Fork** (top-right). This creates a copy under your account.

### 2. Clone your fork locally and create a feature branch

```bash
git clone https://github.com/alexandergolbergwix/w3id.org.git
cd w3id.org
git checkout -b add-mhm-prefix
```

### 3. Create the `mhm/` directory with the `.htaccess` file

```bash
mkdir mhm
cp /Users/alexandergo/Documents/Doctorat/pipeline/docs/w3id/htaccess mhm/.htaccess
```

The `.htaccess` file is already drafted at the path above. It defines
five redirect rules (manuscript, person, work, ontology, landing).

### 4. Commit and push

```bash
git add mhm/.htaccess
git commit -m "Add /mhm/ prefix for Mapping Hebrew Manuscripts (MHM) project"
git push -u origin add-mhm-prefix
```

### 5. Open the pull request

Go to your fork on GitHub. You'll see a "Compare & pull request" button
at the top. Click it. Use this PR title and description:

#### PR title

```
Add /mhm/ prefix for Mapping Hebrew Manuscripts (MHM) project
```

#### PR description

```
This PR registers a new permalink prefix `/mhm/` for the Mapping Hebrew
Manuscripts (MHM) project, a doctoral research project at Bar-Ilan
University that converts Hebrew manuscript catalog records (MARC) into
a CIDOC-CRM/LRMoo aligned ontology (HMO) and publishes the result to
both Wikidata and a project-owned Wikibase Cloud instance.

**Maintainer**: Alexander Goldberg <alexandergo@wix.com>
**Institution**: Bar-Ilan University
**Project repository**: https://github.com/alexandergolbergwix/pipeline
**Wikibase Cloud instance**: https://mhm-hmo.wikibase.cloud
**Ontology**: https://github.com/alexandergolbergwix/pipeline/blob/main/ontology/hebrew-manuscripts.ttl

## What the prefix resolves to

| URL pattern | Target |
|---|---|
| `w3id.org/mhm/` | https://mhm-hmo.wikibase.cloud/ (project landing) |
| `w3id.org/mhm/manuscript/<nli-control-number>` | https://mhm-hmo.wikibase.cloud/wiki/MS_<cn> |
| `w3id.org/mhm/person/<id>` | https://mhm-hmo.wikibase.cloud/wiki/Person_<id> |
| `w3id.org/mhm/work/<id>` | https://mhm-hmo.wikibase.cloud/wiki/Work_<id> |
| `w3id.org/mhm/ontology` | https://raw.githubusercontent.com/alexandergolbergwix/pipeline/main/ontology/hebrew-manuscripts.ttl |

All redirects are temporary (302) so corrections during initial rollout
remain cheap; they will be reviewed and potentially promoted to 303
(See Other) for the ontology IRI once content negotiation is added.

## Usage

The pipeline emits `w3id.org/mhm/manuscript/<cn>` as the value of
[Wikidata P2888 (exact match)](https://www.wikidata.org/wiki/Property:P2888)
on every manuscript item it projects to Wikidata, bridging the public
Wikidata projection to the project's canonical HMO graph.

Expected initial use: ~1,939 entities from the test corpus, scaling to
the full ~70,000 Hebrew manuscript records in the National Library of
Israel catalog over the course of the dissertation.

Thanks for maintaining this service!
```

### 6. Wait for review

A maintainer typically responds within 1–7 days. They may ask for small
adjustments (e.g., switch a 302 to 303 for content negotiation, or
clarify a redirect target). Apply any feedback by pushing more commits
to the same branch; the PR updates automatically.

After merge, the URLs go live within minutes.

### 7. After merge — update the pipeline

Once the PR is merged, switch the pipeline's URI helper from the
Wikibase Cloud URL to the w3id.org permalink:

In `converter/wikidata/property_mapping.py`, change:

```python
HMO_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud"

def hmo_wikibase_page_url(control_number: str) -> str:
    cn = (control_number or "").strip()
    if not cn:
        return ""
    return f"{HMO_WIKIBASE_BASE_URL}/wiki/MS_{cn}"
```

to:

```python
HMO_PERMALINK_BASE = "https://w3id.org/mhm"

def hmo_wikibase_page_url(control_number: str) -> str:
    cn = (control_number or "").strip()
    if not cn:
        return ""
    return f"{HMO_PERMALINK_BASE}/manuscript/{cn}"
```

Update the Phase 1 test in
`tests/unit/test_safety_guards.py::TestP2888EmitsHmoIri` to expect the
new URL:

```python
assert p2888[0].value == "https://w3id.org/mhm/manuscript/990000123"
```

Then rebuild and reinstall the app (`/reinstall-app`).

## Troubleshooting

- **PR sits for >2 weeks without response**: ping the
  [w3id.org maintainers list](https://github.com/perma-id/w3id.org#maintainers)
  by mentioning a recent maintainer in a polite comment on your PR.
- **Maintainer asks for content negotiation on the ontology URI**:
  add the standard Apache `RewriteCond %{HTTP_ACCEPT}` rules for
  `text/turtle` and `application/rdf+xml`. Reference example in the
  schema.org `.htaccess` rules.
- **Apache syntax error in CI**: the PR's automated tests will surface
  this; the maintainer typically pastes the exact line. Most often
  caused by a missing `RewriteEngine on` or a typo in a flag.

## What to expect

| Stage | Time |
|---|---|
| Fork + PR submission | 20 minutes |
| Initial maintainer response | 1–7 days |
| Iteration with feedback | usually 1 round |
| Merge | within 24 hours of approval |
| Live propagation | minutes after merge |

Total: usually 1–2 weeks end to end.
