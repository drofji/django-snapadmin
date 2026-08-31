// snapadmin/static/snapadmin/js/admin.js

(function() {

    var init = function($) {

        // Opt-in only (#JS2a/DECISIONS.md D3): select2 is initialised on a
        // <select> only when it carries this marker, never on an arbitrary
        // form <select>. Stamp `snapadmin-select2` (or `data-snapadmin-select2`)
        // on a widget's attrs to opt a field back in. The old opt-out selector
        // (every <select> except a denylist) reached the changelist's own
        // action dropdown, which the themed admin renders through Alpine
        // (x-model on the <select>); select2 took the element over and set its
        // value via jQuery, which Alpine's binding never observed — the run
        // button stayed hidden forever, with no error anywhere.
        var SELECT2_SELECTOR = "select.snapadmin-select2, select[data-snapadmin-select2]";

        function activateSelect2() {
            // No select2 asset on the page (e.g. a project that hand-copied
            // the base media list and dropped it) — degrade to a plain
            // <select> rather than throwing and taking activateRowClick()
            // down with it.
            if (!$.fn.select2) return;

            $('#changelist-filter select').each(function() {
                var $select = $(this);
                if (!$select.hasClass('select2-hidden-accessible')) {
                    $select.select2({
                        width: 'calc(100% - 30px)',
                        placeholder: $select.find('option:first').text() || '---',
                        allowClear: false,
                        dropdownAutoWidth: true,
                        containerCssClass: 'django-select2-container',
                        minimumResultsForSearch: 7
                    });
                    $select.on('change', function() {
                        var val = $(this).val();
                        window.location.search = val.startsWith('?') ? val : '?' + val;
                    });
                    $select.next('.select2-container').css({
                        'margin': '0 15px',
                        'display': 'block'
                    });
                }
            });
            $(SELECT2_SELECTOR).not('.select2-hidden-accessible').select2({
                allowClear: false,
                minimumResultsForSearch: 7
            });
        }

        function activateRowClick() {
            var rowSelector = ".results table tbody tr, #changelist-form table tbody tr";
            $(rowSelector).each(function() {
                var $row = $(this);
                // Search in priority order: first th link, then any td link (not just first cell),
                // excluding add/history/delete action links to avoid wrong destination
                var $link = $row.find(
                    "th a[href], " +
                    "td.field-id a[href], " +
                    "td:first-child a[href]:not(.deletelink):not(.historylink), " +
                    "td a[href*='/change/']:first, " +
                    "td a[href]:not([href$='/add/']):not([href*='/delete/']):first"
                ).first();

                if (!$link.length) return;

                var url = $link.attr("href");
                if (!url || url === "#") return;

                $row.css('cursor', 'pointer').attr('data-href', url);
                $row.off('click.snapadmin').on('click.snapadmin', function(e) {
                    if ($(e.target).closest(
                        'input[type="checkbox"], input[type="radio"], button, a, .action-select, select'
                    ).length) return;
                    window.location.href = url;
                });
            });
        }

        // Independent ready handlers (#JS2a): jQuery 3's .ready() attaches
        // each callback as its own promise handler, so a TypeError thrown by
        // one (e.g. select2 missing before the guard above existed) does not
        // stop the other from running. A changelist always has at least one
        // <select>, so activateSelect2() failing used to reliably take
        // activateRowClick() down with it.
        $(document).ready(function() {
            activateSelect2();
        });
        $(document).ready(function() {
            activateRowClick();
        });

        $(document).on('formset:added', activateSelect2);
    };

    // Check for jQuery availability
    if (typeof django !== 'undefined' && django.jQuery) {
        init(django.jQuery);
    } else if (typeof jQuery !== 'undefined') {
        init(jQuery);
    } else {
        document.addEventListener('DOMContentLoaded', function() {
            if (typeof django !== 'undefined' && django.jQuery) {
                init(django.jQuery);
            }
        });
    }
})();
