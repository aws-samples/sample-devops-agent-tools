build-DependenciesLayer:
	pip3 install -r requirements.txt -t python/ --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all: --implementation cp
	cp -r python/ $(ARTIFACTS_DIR)/python/
