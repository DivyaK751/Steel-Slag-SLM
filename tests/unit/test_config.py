from pathlib import Path

import pytest
import yaml

from steel_slag_slm import config


@pytest.fixture(autouse=True)
def _ignore_local_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's real .env must not leak into these tests
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)


def _write_config(tmp_path: Path, screening: dict[str, object]) -> Path:
    path = tmp_path / "base.yaml"
    raw = {
        "llm": {"model": "default-model", "temperature": 0.0},
        "registry": {"dataset": "steel_slag", "table": "papers", "location": "US"},
        "screening": screening,
    }
    path.write_text(yaml.safe_dump(raw))
    return path


def test_screening_settings_fall_back_to_llm_defaults_when_model_is_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIGQUERY_PAPERS_TABLE", "proj.ds.papers")
    path = _write_config(
        tmp_path,
        {"prompt_name": "p", "model": None, "batch_size": 5, "max_concurrency": 2},
    )
    settings = config.load_screening_settings(path)
    assert settings.model == "default-model"
    assert settings.papers_table == "proj.ds.papers"


def test_screening_settings_override_llm_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIGQUERY_PAPERS_TABLE", "proj.ds.papers")
    path = _write_config(
        tmp_path,
        {"prompt_name": "p", "model": "pro-model", "batch_size": 5, "max_concurrency": 2},
    )
    assert config.load_screening_settings(path).model == "pro-model"


def test_screening_table_defaults_to_registry_table_in_gcp_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BIGQUERY_PAPERS_TABLE", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    path = _write_config(tmp_path, {"prompt_name": "p", "batch_size": 5, "max_concurrency": 2})
    assert config.load_screening_settings(path).papers_table == "proj.steel_slag.papers"


def test_screening_table_needs_gcp_project_without_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BIGQUERY_PAPERS_TABLE", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    path = _write_config(tmp_path, {"prompt_name": "p", "batch_size": 5, "max_concurrency": 2})
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        config.load_screening_settings(path)


def test_base_config_has_a_valid_screening_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIGQUERY_PAPERS_TABLE", "proj.ds.papers")
    settings = config.load_screening_settings()
    assert settings.model
    assert settings.batch_size > 0


def test_gcp_settings_default_to_vertex_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    settings = config.load_gcp_settings()
    assert (settings.project, settings.location) == ("proj", "global")
    assert config.os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] == "TRUE"


def test_evaluation_table_sits_next_to_papers_table() -> None:
    settings = config.load_evaluation_settings("other-proj.steel_slag.papers_trial")
    assert settings.labels_table == "other-proj.steel_slag.screening_evaluation"
    assert settings.output_dir.name == "evaluation"


def test_pdf_parsing_settings_use_trial_table_and_drive_names() -> None:
    settings = config.load_pdf_parsing_settings()

    assert settings.source_table == "capstone-project-511200.steel_slag.papers_trial"
    assert settings.output_folder_name == "_parsed"
    assert settings.index_file_name == "index.json"