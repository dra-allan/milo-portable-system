/**
 * Image generation model catalog + cost calc + model picker.
 *
 * Based on Google Flow's Imagen family models available via labs.google/fx/tools/flow
 */

export type ImageModelSpec = {
  key: string;
  friendlyName: string;
  cost: number;                 // credits per image
  defaultWidth: number;
  defaultHeight: number;
  supportsSeed: boolean;
  supportsReferenceImages: boolean;
};

/**
 * Raw imageModelName keys actually accepted by Flow's flowMedia:batchGenerateImages
 * endpoint (verified from flow.projectInitialData modelConfig, 2026-08-11):
 *   NARWHAL     = "Nano Banana 2"        (free, the pipeline default)
 *   HARBOR_SEAL = "Nano Banana 2 Lite"
 *   GEM_PIX_2   = "Nano Banana Pro"
 * The old friendly keys (imagen-4 / nano-banana-2 / nano-banana-2-lite / nano-banana-2-pro)
 * are NOT valid raw keys — Flow returns INVALID_ARGUMENT for them.
 */
export const IMAGE_MODELS: Record<string, ImageModelSpec> = {
  // Nano Banana 2 (balanced, free)
  'NARWHAL': {
    key: 'NARWHAL',
    friendlyName: 'Nano Banana 2',
    cost: 0,                    // free on all service tiers
    defaultWidth: 768,
    defaultHeight: 768,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
  // Nano Banana 2 Lite (fastest, cheapest)
  'HARBOR_SEAL': {
    key: 'HARBOR_SEAL',
    friendlyName: 'Nano Banana 2 Lite',
    cost: 0,
    defaultWidth: 512,
    defaultHeight: 512,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
  // Nano Banana Pro (highest quality)
  'GEM_PIX_2': {
    key: 'GEM_PIX_2',
    friendlyName: 'Nano Banana Pro',
    cost: 0,
    defaultWidth: 1024,
    defaultHeight: 1024,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
};

// Friendly aliases the user can pass to `--model`
const IMAGE_MODEL_ALIASES: Record<string, string> = {
  // Nano Banana variants
  'nano-banana-2': 'NARWHAL',
  'nb2': 'NARWHAL',
  'NARWHAL': 'NARWHAL',
  'nano-banana-2-lite': 'HARBOR_SEAL',
  'nb2-lite': 'HARBOR_SEAL',
  'HARBOR_SEAL': 'HARBOR_SEAL',
  'nano-banana-pro': 'GEM_PIX_2',
  'nb2-pro': 'GEM_PIX_2',
  'nano-banana-2-pro': 'GEM_PIX_2',
  'GEM_PIX_2': 'GEM_PIX_2',
};

export function resolveImageModelKey(input: string): string {
  return IMAGE_MODEL_ALIASES[input] || input;
}

export function pickImageModel(override?: string): ImageModelSpec {
  if (override) {
    const resolved = resolveImageModelKey(override);
    const m = IMAGE_MODELS[resolved];
    if (!m) {
      const aliases = Object.keys(IMAGE_MODEL_ALIASES).slice(0, 6).join(', ');
      throw new Error(
        `unknown image model "${override}". Try friendly aliases like: ${aliases}, ... ` +
        `or raw keys: ${Object.keys(IMAGE_MODELS).join(', ')}`,
      );
    }
    return m;
  }
  // Default to nano-banana-2 (balanced)
  return IMAGE_MODELS['NARWHAL'];
}

export function totalImageCost(model: ImageModelSpec, count: number): number {
  return model.cost * Math.max(1, count);
}