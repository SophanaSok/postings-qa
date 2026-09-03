import yaml

from jobbot.config import EXAMPLE_CONFIG, config_from_raw, flow_list, load_config, load_raw, save_raw


def test_config_from_raw_matches_load_config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(EXAMPLE_CONFIG.read_text())
    via_loader = load_config(cfg_file, tmp_path)
    via_raw = config_from_raw(yaml.safe_load(cfg_file.read_text()), tmp_path)
    assert via_raw == via_loader
    assert via_raw.browser.delay_seconds == (1.5, 4.0)
    assert via_raw.qa.salary_bounds_usd_year == (20000, 500000)


def test_load_raw_falls_back_to_example(tmp_path):
    raw = load_raw(tmp_path / "missing.yaml", tmp_path)
    assert raw["search"]["keywords"]


def test_save_raw_keeps_comments_and_changes_only_edited_keys(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(EXAMPLE_CONFIG.read_text())
    raw = load_raw(cfg_file, tmp_path)
    raw["search"]["max_pages"] = 1
    raw["search"]["keywords"] = ["SDET", "QA Engineer"]
    raw["browser"]["delay_seconds"] = flow_list([2.0, 5.0])
    save_raw(raw, cfg_file)

    text = cfg_file.read_text()
    assert "# only ask sites for postings this recent" in text  # inline comment survived
    assert "# random pause between page loads" in text
    assert "delay_seconds: [2.0, 5.0]" in text  # flow style kept
    assert "- SDET" in text

    before = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    after = yaml.safe_load(text)
    assert after["search"]["max_pages"] == 1
    assert after["search"]["keywords"] == ["SDET", "QA Engineer"]
    after["search"]["max_pages"], after["search"]["keywords"], after["browser"]["delay_seconds"] = (
        before["search"]["max_pages"], before["search"]["keywords"], before["browser"]["delay_seconds"])
    assert after == before  # nothing else drifted

    cfg = load_config(cfg_file, tmp_path)
    assert cfg.search.max_pages == 1 and cfg.browser.delay_seconds == (2.0, 5.0)
