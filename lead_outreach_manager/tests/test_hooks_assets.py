"""Guards against the exact bug found while screenshotting the app for
handoff docs: contact.js lived one directory too deep (inside the module
folder rather than the app package root next to hooks.py), so hooks.py's
`doctype_js` reference never resolved to a real file and "Generate Outreach
Email" silently never appeared on the Contact form — no error anywhere,
just a missing button. Frappe's asset symlink (sites/assets/<app> ->
apps/<app>/<app>/public) only ever looks at the package root, so any
future doctype_js/doctype_list_js/etc. entry needs to resolve there too."""
import unittest
from pathlib import Path

APP_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class TestHooksAssetPaths(unittest.TestCase):
	def test_doctype_js_paths_exist_relative_to_app_package_root(self):
		import lead_outreach_manager.hooks as hooks

		doctype_js = getattr(hooks, "doctype_js", {})
		self.assertTrue(doctype_js, "expected at least one doctype_js entry to check")
		for doctype, relative_path in doctype_js.items():
			resolved = APP_PACKAGE_ROOT / relative_path
			self.assertTrue(
				resolved.is_file(),
				f"hooks.doctype_js[{doctype!r}] = {relative_path!r} does not resolve to a real "
				f"file at {resolved} (must sit under {APP_PACKAGE_ROOT}/public/, not the module "
				"folder one level deeper)",
			)
