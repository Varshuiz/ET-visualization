/**
 * Shared City/Town vs Coordinates toggle + Leaflet map picker (ET Setup & Your Regions).
 */
(function (global) {
    function parseJsonScript(id) {
        const el = document.getElementById(id);
        if (!el || !el.textContent) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return null;
        }
    }

    function initLocationInputModes(options) {
        const cfg = options || {};
        const root = cfg.root || document;
        const toggleButtons = root.querySelectorAll(cfg.toggleSelector || '.location-toggle button');
        const sections = root.querySelectorAll(cfg.sectionSelector || '.location-input-modes .input-section');
        const locationTypeInput = root.querySelector(
            '#' + (cfg.locationTypeInputId || 'location_input_mode')
        );
        const latInput = root.querySelector('#' + (cfg.latInputId || 'id_latitude'));
        const lonInput = root.querySelector('#' + (cfg.lonInputId || 'id_longitude'));
        const mapEl = root.querySelector('#' + (cfg.mapElementId || 'locationMap'));
        const provinceSelect = cfg.provinceSelectId
            ? root.querySelector('#' + cfg.provinceSelectId)
            : null;
        const mapCenters = parseJsonScript(cfg.mapCentersScriptId || 'location-map-centers-data') || {};
        const stationsData = cfg.showAlbertaStations
            ? parseJsonScript(cfg.stationsScriptId || 'alberta-stations-data') || []
            : [];

        let farmMap = null;
        let marker = null;

        function refreshMapSizeIfVisible() {
            const coordSection = root.querySelector('#' + (cfg.coordinatesSectionId || 'coordinates-section'));
            if (farmMap && coordSection && coordSection.classList.contains('active')) {
                setTimeout(function () {
                    farmMap.invalidateSize();
                }, 150);
            }
        }

        function setActiveMode(target) {
            toggleButtons.forEach(function (btn) {
                btn.classList.toggle('active', btn.getAttribute('data-target') === target);
            });
            sections.forEach(function (section) {
                section.classList.remove('active');
            });
            const activeSection = root.querySelector('#' + target + '-section');
            if (activeSection) {
                activeSection.classList.add('active');
            }
            if (locationTypeInput) {
                locationTypeInput.value = target === 'place' ? 'city' : target;
            }
            if (cfg.formId) {
                const form = document.getElementById(cfg.formId);
                if (form && global.FormFieldValidation) {
                    global.FormFieldValidation.clearAll(form);
                }
            }
            refreshMapSizeIfVisible();
        }

        function initMap() {
            if (!mapEl || !latInput || !lonInput || typeof global.L === 'undefined' || farmMap) {
                return;
            }
            const province = provinceSelect ? provinceSelect.value : 'Alberta';
            const center = mapCenters[province] || mapCenters.Alberta || [53.5, -114.0];
            const initialLat = parseFloat(latInput.value) || center[0];
            const initialLon = parseFloat(lonInput.value) || center[1];

            farmMap = global.L.map(mapEl).setView([initialLat, initialLon], 6);
            global.L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                maxZoom: 18,
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
                subdomains: 'abcd',
            }).addTo(farmMap);

            marker = global.L.marker([initialLat, initialLon], { draggable: true }).addTo(farmMap);

            function setLatLon(lat, lon) {
                latInput.value = lat.toFixed(4);
                lonInput.value = lon.toFixed(4);
                marker.setLatLng([lat, lon]);
            }

            farmMap.on('click', function (e) {
                setLatLon(e.latlng.lat, e.latlng.lng);
            });

            marker.on('dragend', function (e) {
                const pos = e.target.getLatLng();
                setLatLon(pos.lat, pos.lng);
            });

            stationsData.forEach(function (stn) {
                const stationMarker = global.L.circleMarker([stn.lat, stn.lon], {
                    radius: 6,
                    color: '#0b5f66',
                    weight: 2,
                    fillColor: '#5AAA95',
                    fillOpacity: 0.85,
                }).addTo(farmMap);
                stationMarker.bindTooltip(stn.name, { direction: 'top', opacity: 0.95 });
                stationMarker.on('click', function () {
                    setLatLon(stn.lat, stn.lon);
                    farmMap.panTo([stn.lat, stn.lon]);
                });
            });
        }

        toggleButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                const target = this.getAttribute('data-target');
                if (!target) return;
                setActiveMode(target);
                if (target === 'coordinates') {
                    initMap();
                }
            });
        });

        if (provinceSelect) {
            provinceSelect.addEventListener('change', function () {
                const prov = provinceSelect.value;
                if (farmMap && mapCenters[prov]) {
                    const c = mapCenters[prov];
                    farmMap.setView(c, prov === 'Alberta' ? 6 : 5);
                }
            });
        }

        const initialMode = cfg.initialMode || (locationTypeInput && locationTypeInput.value) || 'place';
        const normalizedMode = initialMode === 'city' ? 'place' : initialMode;
        setActiveMode(normalizedMode);
        if (normalizedMode === 'coordinates') {
            initMap();
        }
    }

    global.initLocationInputModes = initLocationInputModes;
})(window);
