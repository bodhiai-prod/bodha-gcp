import React, { useEffect, useState } from "react";
import { API } from "@/lib/api";
import { getFirebaseToken } from "@/lib/firebase";

export default function BrandingLogo({ paperId, hasLogo, className = "" }) {
  const [src, setSrc] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hasLogo) {
      setSrc(null);
      setFailed(false);
      return undefined;
    }
    let disposed = false;
    let objectUrl = null;
    setSrc(null);
    setFailed(false);
    getFirebaseToken()
      .then((token) => fetch(`${API}/papers/${paperId}/header-image`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }))
      .then((response) => {
        if (!response.ok) throw new Error("Logo unavailable");
        return response.blob();
      })
      .then((blob) => {
        if (disposed) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [paperId, hasLogo]);

  if (!hasLogo) return null;
  if (!src) {
    return (
      <div
        className="paper-branding-logo-loading text-xs text-neutral-500"
        data-testid={failed ? "branding-logo-failed" : "branding-logo-loading"}
      >
        {failed ? "Branding logo unavailable" : "Loading branding logo..."}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt="School logo"
      className={`paper-branding-logo ${className}`}
      data-testid="branding-logo"
    />
  );
}
