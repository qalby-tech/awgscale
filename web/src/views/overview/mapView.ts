"use client";

/**
 * The viewport of the map: a world of fixed size that can be dragged and
 * zoomed; until it is, everything is fitted. Moving is done on the element
 * itself — it happens with every pointer event, and nothing else on the page
 * depends on it.
 */
import { useCallback, useEffect, useRef, type PointerEvent, type RefObject } from "react";

import { mapDims, type MapDims } from "./mapLayout";
import { PointerEventType } from "@/lib/dom";

const ZOOM = { min: 0.4, max: 2.5 } as const;
const MAX_VIEW_HEIGHT = 680;
const DEFAULT_WIDTH = 900;
/** A pointer that moved less than this was a click, not a drag. */
const DRAG_START = 4;
/** The viewport changed by more than this (the side panel opened or closed): fit again. */
const REFIT_WIDTH = 8;
const WHEEL_LINE = 16;
const WHEEL_LIMIT = 30;
const WHEEL_SPEED = 0.01;

interface View {
  scale: number;
  x: number;
  y: number;
  auto: boolean;
  viewportWidth: number;
}
interface Drag {
  id: number;
  x: number;
  y: number;
  viewX: number;
  viewY: number;
  auto: boolean;
  moved: boolean;
}

export const DEFAULT_DIMS = mapDims(DEFAULT_WIDTH);

type ZoomAt = (pointX: number, pointY: number, factor: number) => void;

/** Plain wheel scrolls the page; with Ctrl (or a pinch) it zooms. Bound by hand: it must be able to stop the page's own zoom. */
function useWheelZoom(box: RefObject<HTMLDivElement | null>, zoomAt: ZoomAt): void {
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      // a mouse wheel sends big steps: one notch is one step
      const turned = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * WHEEL_LINE : event.deltaY;
      const limited = Math.max(-WHEEL_LIMIT, Math.min(WHEEL_LIMIT, turned));
      zoomAt(event.clientX - rect.left, event.clientY - rect.top, Math.exp(-limited * WHEEL_SPEED));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [box, zoomAt]);
}

/** Dragging the canvas moves the map; a press let go without moving is a click on the empty canvas. */
function useMapDrag(view: RefObject<View>, drag: RefObject<Drag | null>, apply: () => void, onEmptyClick: () => void) {
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current || !event.isPrimary || event.button !== 0 || (event.target as Element).closest("button,a")) return;
    const current = view.current;
    drag.current = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      viewX: current.x,
      viewY: current.y,
      auto: current.auto,
      moved: false,
    };
    // captured, the release reaches us wherever it happens
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* the pointer is gone already */
    }
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const held = drag.current;
    const current = view.current;
    if (!held || event.pointerId !== held.id) return;
    const movedX = event.clientX - held.x;
    const movedY = event.clientY - held.y;
    if (!held.moved) {
      if (Math.hypot(movedX, movedY) < DRAG_START) return;
      held.moved = true;
      event.currentTarget.classList.add("grab");
    }
    current.x = held.viewX + movedX;
    current.y = held.viewY + movedY;
    current.auto = false;
    apply();
  };
  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    const held = drag.current;
    const current = view.current;
    if (!held || event.pointerId !== held.id) return;
    drag.current = null;
    event.currentTarget.classList.remove("grab");
    if (event.type === PointerEventType.Cancel) {
      // the browser took the gesture: the map goes back to where it was
      current.x = held.viewX;
      current.y = held.viewY;
      current.auto = held.auto;
      apply();
      return;
    }
    if (!held.moved) onEmptyClick();
  };

  return { onPointerDown, onPointerMove, onPointerUp: onPointerEnd, onPointerCancel: onPointerEnd };
}

export function useMapView(
  box: RefObject<HTMLDivElement | null>,
  world: RefObject<HTMLDivElement | null>,
  onEmptyClick: () => void,
) {
  const view = useRef<View>({ scale: 1, x: 0, y: 0, auto: true, viewportWidth: 0 });
  const drag = useRef<Drag | null>(null);

  /** Puts the view's position and scale onto the world element. */
  const apply = useCallback(() => {
    const element = box.current;
    const worldElement = world.current;
    const current = view.current;
    if (element) {
      // focus must not scroll the viewport; a moved map takes touch gestures itself
      element.scrollLeft = 0;
      element.scrollTop = 0;
      element.classList.toggle("free", !current.auto);
    }
    if (worldElement) {
      const shift = "translate(" + current.x.toFixed(1) + "px," + current.y.toFixed(1) + "px)";
      worldElement.style.transform = shift + " scale(" + current.scale.toFixed(3) + ")";
    }
  }, [box, world]);

  /** Fits the world into a viewport of this width: the scale, the height of the viewport, the position. */
  const fit = useCallback((element: HTMLDivElement, viewportWidth: number, dims: MapDims, height: number) => {
    const current = view.current;
    const width = viewportWidth || dims.width;
    const widthScale = Math.min(1, width / dims.width);
    const viewportHeight = Math.min(MAX_VIEW_HEIGHT, Math.round(height * widthScale));
    element.style.height = viewportHeight + "px";
    current.scale = Math.min(widthScale, viewportHeight / height);
    current.x = (width - dims.width * current.scale) / 2;
    current.y = (viewportHeight - height * current.scale) / 2;
  }, []);

  /** After every drawing: measures the viewport and, while nothing was moved by hand, fits the world into it. Returns other dimensions when the world must be laid out again. */
  const settle = useCallback(
    (dims: MapDims, height: number): MapDims | null => {
      const element = box.current;
      const current = view.current;
      if (!element) return null;
      const viewportWidth = element.clientWidth;
      const resized = !!current.viewportWidth && Math.abs(viewportWidth - current.viewportWidth) > REFIT_WIDTH;
      if (!current.auto && resized) current.auto = true;
      current.viewportWidth = viewportWidth;
      if (current.auto) {
        const wanted = mapDims(viewportWidth || DEFAULT_WIDTH);
        if (wanted.width !== dims.width) return wanted;
        fit(element, viewportWidth, dims, height);
      }
      apply();
      return null;
    },
    [box, apply, fit],
  );

  /** Zooms by `factor`, keeping the point of the viewport under (pointX, pointY) where it is. */
  const zoomAt = useCallback(
    (pointX: number, pointY: number, factor: number) => {
      const current = view.current;
      const nextScale = Math.min(ZOOM.max, Math.max(ZOOM.min, current.scale * factor));
      const ratio = nextScale / current.scale;
      current.x = pointX - (pointX - current.x) * ratio;
      current.y = pointY - (pointY - current.y) * ratio;
      current.scale = nextScale;
      current.auto = false;
      apply();
    },
    [apply],
  );
  const zoom = (factor: number) => {
    const element = box.current;
    if (element) zoomAt(element.clientWidth / 2, element.clientHeight / 2, factor);
  };

  useWheelZoom(box, zoomAt);
  const pointer = useMapDrag(view, drag, apply, onEmptyClick);

  const isFitted = useCallback(() => view.current.auto, []);
  const fitAgain = useCallback(() => {
    view.current.auto = true;
  }, []);

  return {
    settle,
    zoom,
    isFitted,
    fitAgain,
    pointer,
  };
}
