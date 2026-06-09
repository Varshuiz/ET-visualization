/**
 * Shared required-field validation for ET Setup, Forecast, and AquaCrop forms.
 */
(function (global) {
    'use strict';

    var ERROR_MSG = 'This field is required.';
    var ERROR_CLASS = 'field-invalid';
    var MSG_CLASS = 'field-error-msg';

    function isEmptyValue(value) {
        if (value === null || value === undefined) return true;
        return String(value).trim() === '';
    }

    function fieldContainer(el) {
        if (!el) return null;
        return (
            el.closest('.form-group')
            || el.closest('.selection-group')
            || el.closest('.flex.flex-col')
            || el.parentElement
        );
    }

    function setInvalid(el, message) {
        if (!el) return;
        el.classList.add(ERROR_CLASS);
        el.setAttribute('aria-invalid', 'true');
        var container = fieldContainer(el);
        if (!container) return;
        var msg = container.querySelector('.' + MSG_CLASS);
        if (!msg) {
            msg = document.createElement('p');
            msg.className = MSG_CLASS;
            msg.setAttribute('role', 'alert');
            container.appendChild(msg);
        }
        msg.textContent = message || ERROR_MSG;
        msg.hidden = false;
    }

    function clearInvalid(el) {
        if (!el) return;
        el.classList.remove(ERROR_CLASS);
        el.removeAttribute('aria-invalid');
        var container = fieldContainer(el);
        if (!container) return;
        var msg = container.querySelector('.' + MSG_CLASS);
        if (msg) {
            msg.hidden = true;
            msg.textContent = '';
        }
    }

    function clearAll(form, fields) {
        (fields || form.querySelectorAll('input, select, textarea')).forEach(function (el) {
            clearInvalid(el);
        });
        var summary = form.querySelector('.form-validation-summary');
        if (summary) summary.hidden = true;
    }

    function showSummary(form, message) {
        var summary = form.querySelector('.form-validation-summary');
        if (!summary) {
            summary = document.createElement('div');
            summary.className = 'form-validation-summary';
            summary.setAttribute('role', 'alert');
            form.insertBefore(summary, form.firstChild);
        }
        summary.textContent = message || 'Please fill in all required fields below.';
        summary.hidden = false;
    }

    function validateField(el) {
        if (!el || el.disabled) return true;
        var value = el.value;
        if (el.type === 'number' && isEmptyValue(value)) {
            setInvalid(el);
            return false;
        }
        if (isEmptyValue(value)) {
            setInvalid(el);
            return false;
        }
        clearInvalid(el);
        return true;
    }

    function validateFields(fields) {
        var ok = true;
        var firstInvalid = null;
        fields.forEach(function (el) {
            if (!validateField(el)) {
                ok = false;
                if (!firstInvalid) firstInvalid = el;
            }
        });
        if (firstInvalid) firstInvalid.focus();
        return ok;
    }

    function bindClearOnInput(fields) {
        fields.forEach(function (el) {
            if (!el) return;
            var evt = el.tagName === 'SELECT' ? 'change' : 'input';
            el.addEventListener(evt, function () {
                if (!isEmptyValue(el.value)) clearInvalid(el);
            });
        });
    }

    function interceptSubmit(form, validateFn) {
        if (!form) return;
        form.setAttribute('novalidate', 'novalidate');
        form.addEventListener(
            'submit',
            function (e) {
                clearAll(form);
                if (!validateFn(form)) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                }
            },
            true
        );
    }

    function isSectionActive(sectionId) {
        var section = document.getElementById(sectionId);
        return section && section.classList.contains('active');
    }

    function validateAcisForm(form) {
        var fields = [
            form.querySelector('#acis_province'),
            form.querySelector('#start_date'),
            form.querySelector('#end_date'),
        ].filter(Boolean);

        if (isSectionActive('place-section')) {
            fields.push(form.querySelector('#place_name'));
        } else if (isSectionActive('township-section')) {
            fields.push(
                form.querySelector('#township'),
                form.querySelector('#range')
            );
        } else if (isSectionActive('coordinates-section')) {
            fields.push(
                form.querySelector('#latitude'),
                form.querySelector('#longitude')
            );
        }

        bindClearOnInput(fields);
        var ok = validateFields(fields);
        if (!ok) showSummary(form, 'Please complete all required location and date fields.');
        return ok;
    }

    function validateForecastForm(form) {
        var citySelect = form.querySelector('#city_select');
        var cityInput = form.querySelector('#citySearchInput');
        var cityField = citySelect || cityInput;
        var province = form.querySelector('#province_select');
        var crop = form.querySelector('#crop_type');
        var soil = form.querySelector('#soil_type');

        var fields = [province, cityField, crop, soil].filter(Boolean);
        bindClearOnInput(fields);

        var ok = validateFields(fields);
        if (ok && cityField) {
            var city = cityField.value.trim();
            var allowed = [];
            try {
                var dataEl = document.getElementById('forecast-cities-data');
                var prov = province ? province.value : 'Alberta';
                var byProv = dataEl ? JSON.parse(dataEl.textContent) : {};
                Object.keys(byProv[prov] || {}).forEach(function (region) {
                    (byProv[prov][region] || []).forEach(function (c) {
                        allowed.push(c);
                    });
                });
            } catch (err) {
                allowed = [];
            }
            if (allowed.length && allowed.indexOf(city) === -1) {
                setInvalid(cityField, 'Select a city from the list.');
                ok = false;
                cityField.focus();
            }
        }

        if (!ok) showSummary(form, 'Please select province, city, crop type, and soil type.');
        return ok;
    }

    function isVisibleField(el) {
        if (!el) return false;
        var panel = el.closest('[id^="panel"]');
        if (panel && panel.style.display === 'none') return false;
        return el.offsetParent !== null || el.getClientRects().length > 0;
    }

    function validateAquacropForm(form) {
        var mode =
            (form.querySelector('input[name="sim_mode"]:checked') || {}).value
            || 'historical_range';
        var histType =
            (form.querySelector('input[name="historical_range_type"]:checked') || {}).value
            || 'custom';

        var fields = [
            form.querySelector('#crop'),
            form.querySelector('#timestep'),
            form.querySelector('#soil'),
            form.querySelector('#city_name'),
        ].filter(Boolean);

        if (mode === 'single_year') {
            fields.push(form.querySelector('input[name="simulation_year"]'));
        } else if (mode === 'historical_range' && histType === 'years') {
            fields.push(
                form.querySelector('input[name="hist_year_start"]'),
                form.querySelector('input[name="hist_year_end"]')
            );
        } else if (
            (mode === 'historical_range' && histType === 'custom')
            || mode === 'forecast'
        ) {
            fields.push(
                form.querySelector('#start_date_input'),
                form.querySelector('#end_date_input')
            );
        }

        fields = fields.filter(isVisibleField);
        bindClearOnInput(fields);
        var ok = validateFields(fields);
        if (!ok) showSummary(form, 'Please complete all required simulation parameters.');
        return ok;
    }

    function initAcisFormValidation() {
        var form = document.getElementById('acisForm');
        if (!form) return;
        interceptSubmit(form, validateAcisForm);
    }

    function initForecastFormValidation() {
        var form = document.getElementById('forecastForm');
        if (!form) return;
        interceptSubmit(form, validateForecastForm);
    }

    function initAquacropFormValidation() {
        var form = document.getElementById('simulationForm');
        if (!form) return;
        interceptSubmit(form, validateAquacropForm);
    }

    function runForecastValidation() {
        var form = document.getElementById('forecastForm');
        if (!form) return true;
        clearAll(form);
        return validateForecastForm(form);
    }

    var api = {
        ERROR_MSG: ERROR_MSG,
        setInvalid: setInvalid,
        clearInvalid: clearInvalid,
        clearAll: clearAll,
        validateField: validateField,
        validateFields: validateFields,
        validateAcisForm: validateAcisForm,
        validateForecastForm: validateForecastForm,
        validateAquacropForm: validateAquacropForm,
        initAcisFormValidation: initAcisFormValidation,
        initForecastFormValidation: initForecastFormValidation,
        initAquacropFormValidation: initAquacropFormValidation,
        runForecastValidation: runForecastValidation,
    };

    global.FormFieldValidation = api;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initAcisFormValidation();
            initForecastFormValidation();
            initAquacropFormValidation();
        });
    } else {
        initAcisFormValidation();
        initForecastFormValidation();
        initAquacropFormValidation();
    }
})(window);
