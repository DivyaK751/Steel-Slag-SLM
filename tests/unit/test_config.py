from pathlib import Path
from typing import get_args

import pytest
import yaml

from steel_slag_slm import config
from steel_slag_slm.schemas import SteelSlagType


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


def test_pdf_parsing_settings_read_approved_papers_and_drive_names() -> None:
    settings = config.load_pdf_parsing_settings()
    assert settings.source_table == "capstone-project-511200.steel_slag.papers"
    assert settings.relevant_labels == ["Yes"]
    assert settings.output_folder_name == "_parsed_approved"
    assert settings.index_file_name == "index.json"


def test_drive_connection_settings_load_without_openalex_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRIVE_FOLDER_ID", "drive-folder")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_FILE", raising=False)

    settings = config.load_drive_connection_settings()

    assert settings.folder_id == "drive-folder"
    assert settings.service_account_file is None
    assert settings.oauth_client_file is None
    assert settings.oauth_token_file == config.DEFAULT_GOOGLE_TOKEN_PATH


def test_abstract_extraction_settings_fall_back_to_llm_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIGQUERY_PAPERS_TABLE", "proj.ds.papers")
    path = tmp_path / "base.yaml"
    raw = yaml.safe_load(_write_config(tmp_path, {}).read_text())
    raw["abstract_extraction"] = {
        "prompt_name": "extract_abstract",
        "model": None,
        "max_pages": 3,
        "batch_size": 20,
        "max_concurrency": 4,
        "call_timeout_s": 120,
    }
    path.write_text(yaml.safe_dump(raw))
    settings = config.load_abstract_extraction_settings(path)
    assert settings.model == "default-model" and settings.max_pages == 3
    assert settings.papers_table == "proj.ds.papers"


def test_figure_reading_settings_use_llm_defaults() -> None:
    settings = config.load_figure_reading_settings()
    assert settings.model  # falls back to llm.model
    assert settings.prices.output_usd_per_million > settings.prices.input_usd_per_million


def test_evaluation_prices_skip_models_without_both_prices(tmp_path: Path) -> None:
    path = tmp_path / "base.yaml"
    raw = {
        "evaluation": {"table": "labels", "output_dir": "out"},
        "pricing": {
            "priced": {"input": 0.5, "output": 2},
            "unpriced": {"input": None, "output": 1},
        },
    }
    path.write_text(yaml.safe_dump(raw))
    settings = config.load_evaluation_settings("p.d.papers", path)
    assert set(settings.prices) == {"priced"}


def test_benchmark_split_is_dev_then_test_without_overlap() -> None:
    settings = config.load_extraction_judge_settings()
    split = config.load_benchmark_split(settings.benchmark_file)
    assert split.papers("dev") == split.dev and split.papers("test") == split.test
    assert not set(split.dev) & set(split.test)
    assert settings.reference_workbook.is_file()


def test_benchmark_split_rejects_a_paper_in_both_splits(tmp_path: Path) -> None:
    path = tmp_path / "split.yaml"
    path.write_text(yaml.safe_dump({"dev": ["W1"], "test": ["W1", "W2"]}))
    with pytest.raises(ValueError, match="both dev and test"):
        config.load_benchmark_split(path)


def test_slag_reference_file_covers_each_cns_slag_type() -> None:
    reference = config.load_slag_reference()
    assert set(reference.slag_types) == {"EAF", "BOF", "Ladle"}
    assert set(reference.slag_types) <= set(get_args(SteelSlagType))
    eaf_cao = reference.slag_types["EAF"].oxides["cao_pct"]
    assert (eaf_cao.min, eaf_cao.max, eaf_cao.avg_min, eaf_cao.avg_max) == (22, 60, 35, 40)
    assert reference.slag_types["BOF"].oxides["cao_pct"].avg_min is None


def _write_slag_reference(tmp_path: Path, raw: dict[str, object]) -> Path:
    path = tmp_path / "slag_reference.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    return path


def _slag_reference(oxide: dict[str, object], phases: list[str]) -> dict[str, object]:
    return {
        "slag_types": {
            "EAF": {
                "label": "EAF slag",
                "process": "Scrap melting",
                "oxides": {"cao_pct": oxide},
                "mineral_phases": phases,
            }
        },
        "phase_names": {"Alite": ["alite", "C3S"], "Spinel": ["spinel"]},
    }


@pytest.mark.parametrize(
    "oxide",
    [
        {"min": 60, "max": 22},
        {"min": 22, "max": 60, "avg_min": 35},
        {"min": 22, "max": 60, "avg_min": 50, "avg_max": 70},
    ],
)
def test_slag_reference_rejects_inconsistent_oxide_ranges(
    tmp_path: Path, oxide: dict[str, object]
) -> None:
    path = _write_slag_reference(tmp_path, _slag_reference(oxide, ["Alite"]))
    with pytest.raises(ValueError):
        config.load_slag_reference(path)


def test_slag_reference_rejects_a_phase_without_names(tmp_path: Path) -> None:
    raw = _slag_reference({"min": 22, "max": 60}, ["Mayenite"])
    with pytest.raises(ValueError, match="missing from phase_names"):
        config.load_slag_reference(_write_slag_reference(tmp_path, raw))


def test_slag_reference_rejects_a_name_under_two_phases(tmp_path: Path) -> None:
    raw = _slag_reference({"min": 22, "max": 60}, ["Alite"])
    raw["phase_names"] = {"Alite": ["alite", "C3S"], "Spinel": ["spinel", "c3s"]}
    with pytest.raises(ValueError, match="listed under Alite and Spinel"):
        config.load_slag_reference(_write_slag_reference(tmp_path, raw))


def test_slag_catalogue_settings_sit_in_the_extraction_dataset() -> None:
    settings = config.load_slag_catalogue_settings()
    raw = yaml.safe_load(config.BASE_CONFIG_PATH.read_text())
    assert settings.dataset == raw["data_extraction"]["dataset"]
    assert settings.table == "slag_catalogue"
    assert settings.heavy_metal_limits == {"cr2o3_pct": 0.5, "v2o5_pct": 0.5}


def test_similar_slag_settings_load_from_base_config() -> None:
    settings = config.load_similar_slag_settings()
    assert settings.top_k == 5
    assert settings.min_shared_phases_cross_type == 3
