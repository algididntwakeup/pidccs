'use client';
/* eslint-disable @next/next/no-img-element -- authenticated blob URLs bypass native img fetch headers */

import { useEffect, useState } from 'react';
import { fetchProtectedAsset } from '@/lib/api';

export default function AuthenticatedImage({
  src,
  alt,
  className,
}: {
  src: string;
  alt: string;
  className?: string;
}) {
  const [objectUrl, setObjectUrl] = useState('');

  useEffect(() => {
    let cancelled = false;
    let pendingUrl = '';
    setObjectUrl('');
    void fetchProtectedAsset(src).then((url) => {
      pendingUrl = url;
      if (cancelled) URL.revokeObjectURL(url);
      else setObjectUrl(url);
    }).catch(() => setObjectUrl(''));
    return () => {
      cancelled = true;
      if (pendingUrl) URL.revokeObjectURL(pendingUrl);
    };
  }, [src]);

  return objectUrl ? (
    <img src={objectUrl} alt={alt} loading="lazy" className={className} />
  ) : (
    <div role="img" aria-label={alt} className={className} />
  );
}
