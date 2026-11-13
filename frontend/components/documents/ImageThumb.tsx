"use client";

import { useEffect, useState } from "react";
import { fetchImageBlob } from "@/lib/data";

type Loaded = { id: string; url?: string; missing?: boolean };

/** Loads image bytes through `apiFetch` so the bearer token is sent, then shows an object URL. */
export default function ImageThumb({ imageId }: { imageId: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    fetchImageBlob(imageId, controller.signal).then(
      (blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setLoaded({ id: imageId, url: objectUrl });
      },
      () => {
        if (controller.signal.aborted) return;
        setLoaded({ id: imageId, missing: true });
      },
    );
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [imageId]);

  const current = loaded && loaded.id === imageId ? loaded : null;
  return (
    <figure className="w-40">
      <div className="flex h-32 items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-50 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        {current === null && "Loading..."}
        {current?.missing && "File missing"}
        {current?.url && (
          // eslint-disable-next-line @next/next/no-img-element -- blob: URL, not optimizable
          <img src={current.url} alt={imageId} className="max-h-full max-w-full object-contain" />
        )}
      </div>
      <figcaption className="mt-1 truncate font-mono text-[11px] text-slate-500 dark:text-slate-400" title={imageId}>
        {imageId}
      </figcaption>
    </figure>
  );
}
