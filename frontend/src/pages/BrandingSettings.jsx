import React, { useEffect, useState } from "react";
import Header from "@/components/Header";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { CheckCircle, ImageSquare, UploadSimple } from "@phosphor-icons/react";

const emptyBranding = {
  school_name: "",
  tagline: "",
  footer: "",
  watermark: "",
  header_image_path: null,
};

export default function BrandingSettings() {
  const [branding, setBranding] = useState(emptyBranding);
  const [crestUrl, setCrestUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let active = true;
    api.get("/branding").then(({ data }) => {
      if (active) setBranding({ ...emptyBranding, ...data });
    }).catch(() => toast.error("Could not load header branding"));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!branding.header_image_path) {
      setCrestUrl("");
      return undefined;
    }
    let active = true;
    api.get("/branding/image", { responseType: "blob" }).then(({ data }) => {
      if (active) setCrestUrl(URL.createObjectURL(data));
    }).catch(() => {
      if (active) setCrestUrl("");
    });
    return () => {
      active = false;
      setCrestUrl(current => {
        if (current) URL.revokeObjectURL(current);
        return "";
      });
    };
  }, [branding.header_image_path]);

  const update = (key) => (event) =>
    setBranding(current => ({ ...current, [key]: event.target.value }));

  const upload = async (file) => {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    setUploading(true);
    try {
      const { data } = await api.post("/branding/image", body);
      setBranding(current => ({ ...current, header_image_path: data.header_image_path }));
      toast.success("School crest uploaded");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "School crest upload failed");
    } finally {
      setUploading(false);
    }
  };

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      const { data } = await api.patch("/branding", branding);
      setBranding(current => ({ ...current, ...data }));
      toast.success("Branding saved");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not save branding");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-5xl mx-auto p-6 md:p-12">
        <div className="overline text-neutral-500 mb-2">// SETTINGS / BRANDING</div>
        <h1 className="font-display text-4xl md:text-5xl mb-3">Your school on every paper.</h1>
        <p className="text-neutral-600 max-w-3xl mb-8">
          Upload your school crest and set the school name, tagline and footer line.
          Every question paper and answer key generated from this account will carry
          this branding automatically.
        </p>

        <form onSubmit={save}>
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            <section className="qp-card lg:col-span-3">
              <div className="overline text-neutral-500 mb-5">// TEXT FIELDS</div>
              <label className="qp-label" htmlFor="settings-school-name">School name</label>
              <input id="settings-school-name" value={branding.school_name} onChange={update("school_name")} className="qp-input" placeholder="St. Xavier's Higher Secondary School" maxLength={200} data-testid="settings-school-name" />
              <p className="text-xs text-neutral-500 mt-2 mb-5">Bold, centred at the top of every paper.</p>

              <label className="qp-label" htmlFor="settings-tagline">Tagline / address</label>
              <input id="settings-tagline" value={branding.tagline} onChange={update("tagline")} className="qp-input" placeholder="Estd. 1858 · Mumbai · Affiliated to CISCE" maxLength={300} data-testid="settings-tagline" />
              <p className="text-xs text-neutral-500 mt-2 mb-5">Small italic line under the school name.</p>

              <label className="qp-label" htmlFor="settings-footer">Footer stamp</label>
              <input id="settings-footer" value={branding.footer} onChange={update("footer")} className="qp-input" placeholder="© 2026 · Confidential — for enrolled students only" maxLength={300} data-testid="settings-footer" />
              <p className="text-xs text-neutral-500 mt-2 mb-6">Appears at the bottom of every page, next to the page number.</p>

              <label className="qp-label" htmlFor="settings-watermark">Watermark</label>
              <input id="settings-watermark" value={branding.watermark} onChange={update("watermark")} className="qp-input" placeholder="School name or CONFIDENTIAL" maxLength={100} data-testid="settings-watermark" />
              <p className="text-xs text-neutral-500 mt-2 mb-6">Displayed lightly behind the questions and included in the PDF.</p>

              <button type="submit" disabled={saving} className="qp-btn qp-btn-primary" data-testid="save-settings-branding">
                <CheckCircle size={16} weight="bold" /> {saving ? "Saving..." : "Save branding"}
              </button>
            </section>

            <section className="qp-card lg:col-span-2">
              <div className="overline text-neutral-500 mb-5">// SCHOOL CREST</div>
              <div className="branding-crest-preview">
                {crestUrl ? <img src={crestUrl} alt="School crest" /> : <><ImageSquare size={34} weight="regular" /><span>NO CREST UPLOADED</span></>}
              </div>
              <label htmlFor="settings-header-image" className="qp-btn qp-btn-secondary w-full mt-4 cursor-pointer">
                <UploadSimple size={16} weight="bold" /> {uploading ? "Uploading..." : "Upload crest"}
              </label>
              <input id="settings-header-image" type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" onChange={(e) => upload(e.target.files?.[0])} className="sr-only" disabled={uploading} data-testid="settings-header-image" />
              <p className="text-xs text-neutral-500 mt-4">PNG or JPEG, up to 5 MB. The crest is preserved as a paper snapshot when branding is applied or a paper is generated.</p>
            </section>
          </div>

          <section className="qp-card mt-6">
            <div className="overline text-neutral-500 mb-5">// PREVIEW ON PAPER</div>
            <div className="branding-paper-preview">
              <div className="branding-paper-header">
                {crestUrl && <img src={crestUrl} alt="" />}
                <div>
                  <strong>{branding.school_name || "Your school name"}</strong>
                  {branding.tagline && <em>{branding.tagline}</em>}
                </div>
              </div>
              <div className="branding-paper-title">Physics · Class 10 · Mock</div>
              <div className="branding-paper-meta">Class: Class 10&nbsp;&nbsp; Subject: Physics&nbsp;&nbsp; Marks: 80</div>
              {branding.footer && <div className="branding-paper-footer">{branding.footer}<span>Page 1</span></div>}
              {branding.watermark && <div className="branding-paper-watermark">{branding.watermark}</div>}
            </div>
            <p className="text-xs text-neutral-500 mt-4">Nothing set yet — your PDFs will render without a header block or footer.</p>
          </section>
        </form>
      </main>
    </div>
  );
}
