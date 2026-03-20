.PHONY: install run build clean

# Install the package in editable (development) mode.
install:
	pip install -e .

# Launch the Streamlit web app via the package entry point.
run:
	dne

# Build a distributable wheel and source distribution.
build:
	python -m pip install --quiet build
	python -m build

# Remove all generated build artifacts.
clean:
	rm -rf build dist *.egg-info src/*.egg-info
