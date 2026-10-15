import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "tools" / "workday-assistant"


def test_extension_has_expected_files():
    expected = {
        "manifest.json",
        "background.js",
        "ai_server.py",
        "ai-answer-schema.json",
        "defaults.js",
        "content.js",
        "popup.html",
        "popup.js",
        "options.html",
        "options.js",
        "styles.css",
        "README.md",
    }
    assert expected <= {path.name for path in EXTENSION.iterdir()}


def test_manifest_scope_is_workday_ibm_and_simple_ats_only():
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert "https://*.myworkdayjobs.com/*" in manifest["host_permissions"]
    assert manifest["content_scripts"][0]["matches"] == ["https://*.myworkdayjobs.com/*"]
    assert manifest["content_scripts"][1]["matches"] == ["https://careers.ibm.com/*"]
    assert "https://jobs.ashbyhq.com/*" in manifest["content_scripts"][2]["matches"]
    assert "tabs" not in manifest["permissions"]
    assert "debugger" in manifest["permissions"]


def test_review_first_boundary_is_explicit():
    content = (EXTENSION / "content.js").read_text()
    popup = (EXTENSION / "popup.js").read_text()
    readme = (EXTENSION / "README.md").read_text()
    assert 'input:not([type="hidden"]):not([type="submit"])' in content
    assert "SENSITIVE" in content
    assert "never advances or submits" in (EXTENSION / "popup.html").read_text().lower()
    assert "Submit" not in popup
    assert "submit it yourself" in readme


def test_account_setup_is_explicit_and_bounded():
    background = (EXTENSION / "background.js").read_text()
    content = (EXTENSION / "content.js").read_text()
    popup = (EXTENSION / "popup.html").read_text()
    all_source = "\n".join(path.read_text() for path in EXTENSION.iterdir() if path.suffix in {".js", ".html", ".json", ".md"})
    assert 'chrome.storage.session' in background
    assert 'exactControl("Apply")' in content
    assert 'exactControl("Autofill with Resume")' in content
    assert 'exactControl("Create Account")' in content
    assert 'exactControl("Sign In")' in content
    assert "accountPassword" in all_source
    assert 'exactControl("Next")' not in content
    assert 'exactControl("Submit")' not in content


def test_ai_bridge_is_local_and_review_bounded():
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    server = (EXTENSION / "ai_server.py").read_text()
    content = (EXTENSION / "content.js").read_text()
    assert "http://127.0.0.1:8766/*" in manifest["host_permissions"]
    assert '("127.0.0.1", 8766)' in server
    assert "--output-schema" in server
    assert "SENSITIVE.test(label)" in content
    assert 'message?.action === "aiFill"' in content
    assert 'message?.action === "copilotStart"' in content
    assert "APPLICATION COPILOT" in content


def test_known_authorization_defaults_match_canonical_profile():
    defaults = (EXTENSION / "defaults.js").read_text()
    assert 'workAuthorizedUS: "Yes"' in defaults
    assert 'sponsorshipRequired: "No"' in defaults
    assert 'phoneCountryCode: "United States of America (+1)"' in defaults
    assert 'accountPassword: ""' in defaults  # never ship a real password
    assert 'graduationYear: ""' in defaults
    assert 'hearAboutUs: "LinkedIn"' in defaults
    assert 'previouslyWorkedForEmployer: "No"' in defaults
    assert 'phoneType: "Main/Home"' in defaults
    assert 'resumePath: ""' in defaults
    assert 'if (!profile.resumePath)' in defaults


def test_engine_never_advances_or_submits_and_answers_are_in_sync():
    engine = (EXTENSION / "engine.js").read_text()
    assert "never clicks Continue" in engine.lower() or "never clicks continue" in engine.lower()
    for forbidden in ('"Submit"', '"Continue"', '"Next"'):
        assert forbidden not in engine
    import sys
    sys.path.insert(0, str(EXTENSION))
    import build_standing_answers
    assert (EXTENSION / "standing-answers.js").read_text() == build_standing_answers.build()


def test_standing_answers_never_fill_attestations():
    source = ROOT / "profile" / "application-defaults.json"
    if not source.exists():
        source = ROOT / "profile" / "application-defaults.example.json"
    data = json.loads(source.read_text())
    assert any("i certify" in rule for rule in data["never_fill"])
    assert not any("certify" in entry["q"] for entry in data["qa"])
    assert "password" not in json.dumps(data["contact"]).lower()
