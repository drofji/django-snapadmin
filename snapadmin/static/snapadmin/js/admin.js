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
            // Target all changelist tables across all apps
            $(".results table tbody tr, #changelist-form table tbody tr").each(function() {
                var $row = $(this);
                // Look for the primary link (usually in the first header cell or first cell)
                var $link = $row.find("th a, td.field-id a, td:first-child a").first();

                if ($link.length) {
                    $row.css('cursor', 'pointer');
                    $row.off('click').on('click', function(e) {
                        // Don't trigger if clicking on a checkbox, button, or another link
                        if ($(e.target).closest('input, button, a, .action-select').length) return;

                        var url = $link.attr("href");
                        if (url) {
                            window.location = url;
                        }
                    });
                }
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