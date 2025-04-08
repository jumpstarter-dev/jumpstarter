# Minimal makefile for Sphinx documentation
#

# You can set these variables from the command line, and also
# from the environment for the first two.
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build
HOST          ?= 127.0.0.1
PORT          ?= 8000

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# Define custom targets as PHONY to prevent them from being caught by the pattern rule
.PHONY: help Makefile serve multiversion redirect serve-multiversion

# Catch-all target: route all unknown targets to Sphinx using the new
# "make mode" option.  $(O) is meant as a shortcut for $(SPHINXOPTS).
%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

serve:
	sphinx-autobuild --host $(HOST) --port $(PORT) "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

multiversion:
	sphinx-multiversion "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
	@echo "Creating redirect index.html to main/index.html"
	@echo '<!DOCTYPE html>' > "$(BUILDDIR)/index.html"
	@echo '<html><head><meta http-equiv="refresh" content="0; url=main/index.html" />' >> "$(BUILDDIR)/index.html"
	@echo '<title>Redirect to main documentation</title></head>' >> "$(BUILDDIR)/index.html"
	@echo '<body><p>Redirecting to <a href="main/index.html">main documentation</a>...</p></body></html>' >> "$(BUILDDIR)/index.html"

serve-multiversion:
	@echo "Serving multiversion documentation using the HOST variable"
	@cd "$(BUILDDIR)" && python -m http.server $(PORT) --bind $(HOST)