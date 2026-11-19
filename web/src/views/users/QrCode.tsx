"use client";

/** A QR code drawn with a whole number of screen pixels per module, and its full-screen view. */
import { useEffect, useRef, useState, type KeyboardEvent, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { Key } from "@/lib/dom";

/** The server draws four image pixels per module of the code. */
const IMAGE_PX_PER_MODULE = 4;
const MIN_SCREEN_PX_PER_MODULE = 2;
const PANEL_PADDING = 40;
const SCREEN_MARGIN = 24;

/** As large as fits into `available`, in whole pixels per module. */
function fit(img: HTMLImageElement, available: number): void {
  const modules = img.naturalWidth / IMAGE_PX_PER_MODULE;
  const side = modules * Math.max(MIN_SCREEN_PX_PER_MODULE, Math.floor(available / modules)) + "px";
  img.style.width = side;
  img.style.height = side;
}

function Zoom({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const refit = () => {
      if (img.current?.naturalWidth) fit(img.current, Math.min(innerWidth, innerHeight) - SCREEN_MARGIN);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === Key.Escape) {
        event.stopPropagation();
        onClose();
      }
    };
    box.current?.focus();
    refit();
    addEventListener("resize", refit);
    document.addEventListener("keydown", onKey, true);
    return () => {
      removeEventListener("resize", refit);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [onClose]);
  return createPortal(
    <div ref={box} className="qr-zoom" tabIndex={0} role="dialog" aria-label={alt} onClick={onClose}>
      <img
        ref={img}
        src={src}
        alt={alt}
        onLoad={event => fit(event.currentTarget, Math.min(innerWidth, innerHeight) - SCREEN_MARGIN)}
      />
    </div>,
    document.body,
  );
}

export function QrCode({ png, app }: { png: string; app: string }) {
  const [zoomed, setZoomed] = useState(false);
  const src = "data:image/png;base64," + png;
  const alt = "QR-код для " + app;
  const onLoad = (event: SyntheticEvent<HTMLImageElement>) => {
    const panel = event.currentTarget.parentElement;
    if (panel) fit(event.currentTarget, panel.clientWidth - PANEL_PADDING);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === Key.Enter || event.key === Key.Space) {
      event.preventDefault();
      setZoomed(true);
    }
  };
  return (
    <>
      <img
        className="qr"
        alt={alt}
        title="Увеличить"
        tabIndex={0}
        role="button"
        src={src}
        onLoad={onLoad}
        onClick={() => setZoomed(true)}
        onKeyDown={onKey}
      />
      {zoomed ? <Zoom src={src} alt={alt} onClose={() => setZoomed(false)} /> : null}
    </>
  );
}
