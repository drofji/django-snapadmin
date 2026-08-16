"""
Translations for the Unfold theme's own interface strings.

``django-unfold`` (the optional ``[theme]`` extra) ships **no ``locale/`` directory at all**, so
every string it renders itself — "All applications", "Apply Filters", "No results found",
"Select action", the command-palette hints — stays English no matter what ``LANGUAGE_CODE`` says.
On a themed admin that is most of the chrome around a page whose own labels are translated, which
is exactly the "half the page is in the wrong language" effect users report.

Django resolves a msgid against the catalogs of *every* installed application, so a string another
app never translated can be supplied here: SnapAdmin's own catalogs answer for it, and Unfold's
templates render translated without patching Unfold. The declarations below are no-op
``gettext_lazy`` calls whose only job is to be **extractable** — ``makemessages`` scans source, not
the theme's templates, so without them the next regeneration would drop these msgids as obsolete
and the translations would silently disappear.

Listed here are only the strings Django's own admin catalogs do **not** already cover (checked
against django-unfold 0.99 on Django 6.0 across all ten shipped locales). "Save", "Delete", "Yes",
"Log out" and friends come from ``django.contrib.admin`` already translated — overriding those here
would put SnapAdmin in charge of wording it has no business owning. If a future Unfold release
renames a string, the stale msgid here simply stops matching; nothing breaks, and no test is pinned
to the theme's markup.
"""

from django.utils.translation import gettext_lazy as _

#: Unfold interface strings SnapAdmin translates on the theme's behalf.
#:
#: Scope is the admin SnapAdmin actually renders — the shell, changelists, forms, filters,
#: the command palette and the login/logout screens. Strings that belong to Unfold's optional
#: contribs (import/export, impersonate, object history, guardian permissions) and to its own
#: rich-text toolbar are deliberately left out: SnapAdmin does not wire those surfaces up
#: (rich text goes through CKEditor 5), so translating them would be maintenance for markup
#: this package never shows.
THEME_STRINGS = (
    _("Action"),
    _("Add new item"),
    _("Add row"),
    _("After you've created a user, you’ll be able to edit more user options."),
    _("All applications"),
    _("Apply Filters"),
    _("Cancel"),
    _("Choose file to upload"),
    _("Click to cancel"),
    _("Click to download"),
    _("Collapse"),
    _("Current file"),
    _("Dark"),
    _("Date"),
    _("Date from"),
    _("Date to"),
    _("Default"),
    _("Edit"),
    _("Expand row"),
    _("False"),
    _("Filters"),
    _("Forgotten your password or username?"),
    _("From"),
    _("General"),
    _("Go back"),
    _("Image preview"),
    _("Light"),
    _("More actions"),
    _("Navigate"),
    _("New"),
    _("Next"),
    _("No"),
    _("No data"),
    _("No results found"),
    _("Non field specific"),
    _("Not enough data."),
    _("Nothing matched your search"),
    _("Object"),
    _("Previous"),
    _("Recent searches"),
    _("Record picture"),
    _("Reset filters"),
    _("Reset to default"),
    _("Return to site"),
    _("Row"),
    _("Run"),
    _("Run the selected action"),
    _("Search apps and models..."),
    _("Select"),
    _("Select action"),
    _("Select action to run"),
    _("Select all rows"),
    _("Select currency"),
    _("Select format"),
    _("Select record"),
    _("Select value"),
    _("System"),
    _("This item will be deleted."),
    _("This page yielded into no results. Create a new item or reset your filters."),
    _("To"),
    _("Toggle password visibility"),
    _("True"),
    _("Type to search"),
    _("Update"),
    _("Value"),
    _("View traceback"),
    _("Welcome"),
    _("You can not create nested object without parent"),
    _("You have been successfully logged out from the administration"),
    _("hide warning"),
)
