PYTHON ?= python3

.PHONY: run demo test appimage icon clean

run:
	$(PYTHON) -m sc_hammer

demo:
	$(PYTHON) -m sc_hammer --demo

test:
	$(PYTHON) -m unittest discover -s tests -v

appimage:
	packaging/build-appimage.sh

icon:
	$(PYTHON) tools/make_icon.py

clean:
	rm -rf build dist
