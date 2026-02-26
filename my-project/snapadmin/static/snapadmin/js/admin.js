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
            $("#changelist-form table tbody tr").each(function() {
                var $row = $(this);
                var $link = $row.find("th > a").first();

                if ($link.length) {
                    $row.css('cursor', 'pointer');
                    $row.on('click', function(e) {
                        if ($(e.target).is('input, button, a, .action-select')) return;
                        window.location = $link.attr("href");
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