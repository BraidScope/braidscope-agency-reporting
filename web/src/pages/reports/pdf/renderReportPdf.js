// SPDX-License-Identifier: AGPL-3.0-only
// Turns a report definition into a PDF Blob. @react-pdf/renderer is large, so
// it (and the document component) load only when someone actually exports.

// react-pdf embeds PNG/JPEG only; SVG or remote logos are rasterised first.
async function prepareLogo(logo) {
  if (!logo) return null;
  if (/^data:image\/(png|jpe?g);base64,/i.test(logo)) return logo;
  try {
    const image = await new Promise((resolve, reject) => {
      const element = new window.Image();
      element.crossOrigin = "anonymous";
      element.onload = () => resolve(element);
      element.onerror = reject;
      element.src = logo;
    });
    const size = 160;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const scale = Math.min(size / image.width, size / image.height);
    const width = image.width * scale;
    const height = image.height * scale;
    canvas.getContext("2d").drawImage(image, (size - width) / 2, (size - height) / 2, width, height);
    return canvas.toDataURL("image/png");
  } catch {
    // A logo that cannot be embedded must not block the export; the PDF falls
    // back to the agency initial.
    return null;
  }
}

export async function renderReportPdf(input) {
  const [{ pdf }, { default: ReportDocument }, { createElement }] = await Promise.all([
    import("@react-pdf/renderer"),
    import("./ReportDocument.jsx"),
    import("react"),
  ]);
  const logoDataUrl = await prepareLogo(input.branding?.logo);
  const meta = { pages: 1 };
  const blob = await pdf(createElement(ReportDocument, { ...input, logoDataUrl, meta })).toBlob();
  return { blob, pages: meta.pages };
}
