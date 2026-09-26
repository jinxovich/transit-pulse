import { useEffect, useRef, useState } from "react";
import maplibregl, { type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useQuery } from "@tanstack/react-query";
import type { FeatureCollection } from "geojson";
import type { Network } from "@contract";

const STYLE_URL = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const BLANK_STYLE: StyleSpecification = {
    version: 8,
    sources: {},
    layers: [{ id: "bg", type: "background", paint: { "background-color": "#0B0E13" } }],
    };

    async function fetchNetwork(): Promise<Network> {
    const res = await fetch("/api/v1/network");
    if (!res.ok) throw new Error(`network: ${res.status}`);
    return res.json();
    }

    async function loadStyle(): Promise<StyleSpecification> {
    try {
        const res = await fetch(STYLE_URL, { signal: AbortSignal.timeout(3500) });
        if (!res.ok) throw new Error(String(res.status));
        return await res.json();
    } catch {
        console.info("Подложка недоступна, работаем без неё");
        return BLANK_STYLE;
    }
    }

    export function MapView() {
    const container = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const [ready, setReady] = useState(false);
    const network = useQuery({ queryKey: ["network"], queryFn: fetchNetwork, staleTime: Infinity });

    //Создаём карту 
    useEffect(() => {
        let cancelled = false;
        let map: maplibregl.Map | null = null;

        loadStyle().then((style) => {
        if (cancelled || !container.current) return;
        map = new maplibregl.Map({
            container: container.current,
            style,
            center: [37.62, 55.75],
            zoom: 10,
            dragRotate: false,
            attributionControl: { compact: true },
        });
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        map.on("load", () => {
            mapRef.current = map;
            setReady(true);
        });
        });

        return () => {
        cancelled = true;
        map?.remove();
        mapRef.current = null;
        };
    }, []);

    // Рисуем маршруты
    useEffect(() => {
        const map = mapRef.current;
        const net = network.data;
        if (!ready || !map || !net || map.getSource("routes")) return;

        const routes: FeatureCollection = {
        type: "FeatureCollection",
        features: net.segments.map((s) => ({
            type: "Feature",
            properties: { route: s.route_id },
            geometry: s.geometry,
        })),
        };
        map.addSource("routes", { type: "geojson", data: routes });
        map.addLayer({
        id: "routes-base",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
            "line-color": "#2A3342",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 14, 2.5],
        },
        });

        const [minLon, minLat, maxLon, maxLat] = net.bbox;
        map.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 40, duration: 0 });
    }, [ready, network.data]);

    return <div ref={container} className="map" />;
}