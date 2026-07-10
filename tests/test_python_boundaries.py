from scripts.check_python_boundaries import main


def test_production_python_roots_use_api_only_data_boundaries():
    assert main() == 0
