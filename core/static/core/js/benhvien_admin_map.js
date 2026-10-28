(() => {
    const defaultLat = 10.77;
    const defaultLon = 106.7;
    const defaultZoom = 12;
    const targetFieldId = "id_vi_tri";

    const forceCenter = (map) => {
        if (!map || !window.ol) {
            return;
        }
        const view = map.getView();
        if (!view) {
            return;
        }
        const center = window.ol.proj.transform(
            [defaultLon, defaultLat],
            "EPSG:4326",
            view.getProjection()
        );
        view.setCenter(center);
        view.setZoom(defaultZoom);
    };

    const hookMapWidget = () => {
        if (!window.MapWidget || !window.ol) {
            return false;
        }

        const OriginalMapWidget = window.MapWidget;
        if (OriginalMapWidget.__benhvienPatched) {
            return true;
        }

        class PatchedMapWidget extends OriginalMapWidget {
            constructor(options) {
                super(options);
                if (options && options.id === targetFieldId) {
                    setTimeout(() => forceCenter(this.map), 0);
                }
            }
        }

        PatchedMapWidget.__benhvienPatched = true;
        PatchedMapWidget.layerBuilder = OriginalMapWidget.layerBuilder;
        window.MapWidget = PatchedMapWidget;

        return true;
    };

    if (!hookMapWidget()) {
        document.addEventListener("DOMContentLoaded", () => {
            const timer = setInterval(() => {
                if (hookMapWidget()) {
                    clearInterval(timer);
                }
            }, 50);
            setTimeout(() => clearInterval(timer), 5000);
        });
    }
})();
