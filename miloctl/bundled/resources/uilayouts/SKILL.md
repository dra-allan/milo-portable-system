# UILayouts Design Library

Source: https://github.com/ui-layouts/uilayouts.git
Origin: bundled
Author: Milo
Vendored: 2026-08-03

89 React/Shadcn/Tailwind component blocks across 11 sections. Use as
design reference when building vanilla HTML/CSS websites for the
website-flip pipeline. Each site must pick a unique combination of layout
patterns so no two customer sites look alike.

## Section index

| Section | Files | Variants |
|---|---|---|
| hero-section | 7 | ai-ecommerce, ai-infrastructure, ai-value-proposition, digital-success, financial, share-app, social-app, team-integration |
| feature-section | 8 | bento, flow, hero, highlights, nature, platform, service, velocity |
| about-section | 11 | agency, architecture, bento, business, creative, ecommerce, experience, me, mission, sass, vision, whyus |
| testimonial-section | 8 | basic, carousel, chat-interface, creative, marque, messenger, spotlight, stack |
| pricing-section | 6 | grow-business, growth-plans, overview, product-packs, startup-plans, subscription-details |
| stats-section | 6 | advanced-stats, banner, bento, bold, details, minimal, section |
| faq-section | 6 | founder, glass-card, interactive-preview, journey, minimilastic, tabbed-explorer |
| team-section | 9 | classic, clippath, expert, magic, modern, social, synth, talent, troops, vr |
| footer-section | 7 | bento, bold, detailed, hero, minimal, privilege, simple |
| experience-section | 5 | creative, customer, impact, portfolio, work |
| blog-section | 1 | entrepreneurs |

## How Milo uses this

When building a site for a new prospect:

1. Skim relevant sections and pick a component variant that fits the business
2. Translate the React/Tailwind component to vanilla HTML/CSS — adapt colors, copy, and layout to the business' brand
3. Ensure each new site uses a DIFFERENT variant from the same section category to avoid visual repetition
4. Combine sections into a single-page layout: hero + feature + about + stats + testimonial + contact form + footer
5. Assets/screenshots in assets/ for visual reference

## Dependencies (for the React version - not needed for vanilla HTML translation)
npm: motion, clsx, tailwind-merge