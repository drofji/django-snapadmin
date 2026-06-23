// drofji_admin/static/drofji_admin/admin.js

(function() {

    var init = function($) {

        function activateSelect2() {

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
            $('select').not('.admin-autocomplete, .select2-hidden-accessible').select2({
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

        $(document).ready(function() {
            activateSelect2();
            activateRowClick();
        });

        $(document).on('formset:added', activateSelect2);
    };

    // Проверка наличия jQuery
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