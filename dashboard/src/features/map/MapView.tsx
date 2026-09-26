import { useEffect, useRef, useState } from "react";
import maplibregl, { type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useQuery } from "@tanstack/react-query";
import type { FeatureCollection, Point } from "geojson";
import type { Network, RiskLevel, VehicleState } from "@contract";
import { fetchConfig } from "../../api/config";
import { useStream } from "../../store/stream";
import { iconName, registerIcons } from "./icons";

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
    // Кто рисуется поверх: красные выше всех, устаревшие ниже.
const DRAW_ORDER: Record<RiskLevel, number> = { red: 5, yellow: 4, early: 3, green: 2, none: 1 };

    function vehiclesToGeoJson(vehicles: Record<string, VehicleState>): FeatureCollection<Point> {
        const features: FeatureCollection<Point>["features"] = [];
        for (const v of Object.values(vehicles)) {
            if (v.lon == null || v.lat == null) continue; // координат ещё нет — пропускаем
            const risk: RiskLevel = v.kind === "scheduled" ? v.risk_level : "none";
            features.push({
            type: "Feature",
            properties: {
                id: v.vehicle_id,
                icon: iconName(risk, v.stale, v.kind === "unknown", v.heading != null),
                heading: v.heading ?? 0,
                order: DRAW_ORDER[risk] - (v.stale ? 5 : 0),
            },
            geometry: { type: "Point", coordinates: [v.lon, v.lat] },
            });
        }
        return { type: "FeatureCollection", features };
    }
    export function MapView() {
    const container = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const [ready, setReady] = useState(false);
    const network = useQuery({ queryKey: ["network"], queryFn: fetchNetwork, staleTime: Infinity });
    const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig, staleTime: Infinity });

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
    // Слой ТС и подписка на поток
useEffect(() => {
  const map = mapRef.current;
  const colors = config.data?.colors;
  if (!ready || !map || !colors) return;

  registerIcons(map, colors);
  if (!map.getSource("vehicles")) {
    map.addSource("vehicles", { type: "geojson", data: vehiclesToGeoJson(useStream.getState().vehicles) });
    map.addLayer({
      id: "vehicles",
      type: "symbol",
      source: "vehicles",
      layout: {
        "icon-image": ["get", "icon"],
        "icon-rotate": ["get", "heading"],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "symbol-sort-key": ["get", "order"],
        "icon-size": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 14, 1.1],
        },
        });
    }

    // Подписка в обход React: новые ТС сразу в карту, без перерисовки компонента
    const unsubscribe = useStream.subscribe((state, prev) => {
        if (state.vehicles === prev.vehicles) return;
        const source = map.getSource("vehicles") as maplibregl.GeoJSONSource | undefined;
        source?.setData(vehiclesToGeoJson(state.vehicles));
    });
    return unsubscribe;
    }, [ready, config.data]);
    return <div ref={container} className="map" />;
}