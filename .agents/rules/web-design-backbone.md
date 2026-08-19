---
description: Rule cốt lõi bắt buộc về thiết kế web. AI agent PHẢI tuân thủ 100% khi tạo/chỉnh sửa file web.
globs: "**/*.{html,css,js,jsx,tsx,ts,vue,svelte,astro}"
trigger: always_on
---

# Web Design Backbone Rule

> **LUẬT KHÔNG THỂ THƯƠNG LƯỢNG.** Vi phạm bất kỳ mục nào → sản phẩm **FAILED**.

## 1. Design Philosophy — Triết lý thiết kế

- **Premium First** — PHẢI trông cao cấp, WOW ngay cái nhìn đầu tiên.
- **Alive, Not Static** — Sống động, có chuyển động, có phản hồi tương tác.
- **TUYỆT ĐỐI KHÔNG** tạo giao diện plain, flat, generic kiểu "bài tập sinh viên".
- Tự hỏi: Design này đặt cạnh Apple/Stripe/Linear có xấu hổ không?

## 2. Layout & Spacing — Bố cục & Khoảng cách

- Dùng **CSS Grid / Flexbox** — KHÔNG float, KHÔNG table layout.
- Container: `max-width: 1200-1400px; margin: 0 auto`.
- Spacing theo bội số 4px/8px: `4, 8, 12, 16, 24, 32, 48, 64, 80, 96, 128`.
- Section padding: ≥80px (desktop), ≥48px (mobile).
- ❌ Không số lẻ (7px, 13px). Không `position: absolute` bừa bãi.

## 3. Typography — Kiểu chữ

- **LUÔN dùng Google Font** — KHÔNG BAO GIỜ font mặc định trình duyệt.
  - Modern: Inter, Plus Jakarta Sans, Outfit, Space Grotesk
  - Elegant: Playfair Display, Cormorant Garamond
  - Friendly: Poppins, DM Sans, Nunito
- Tối đa **2 font** (heading + body). Line-height: body 1.6–1.8, heading 1.1–1.3.
- Heading lớn: letter-spacing âm (-0.02em). Font-weight đa dạng: 400/500/600/700.
- ❌ Không font-size < 14px cho body. PHẢI có visual hierarchy rõ ràng.

## 4. Color System — Hệ thống màu sắc

- **BẮT BUỘC** dùng CSS Custom Properties:
  ```css
  :root {
    --color-primary: ...; --color-primary-light: ...; --color-primary-dark: ...;
    --color-gray-50 → --color-gray-950;
    --color-success: ...; --color-warning: ...; --color-error: ...;
    --color-bg: ...; --color-surface: ...; --color-surface-elevated: ...;
    --color-border: ...;
    --color-text-primary: ...; --color-text-secondary: ...; --color-text-muted: ...;
  }
  ```
- KHÔNG màu generic thô (`#ff0000`, `#0000ff`). Luôn dùng màu đã curate.
- Gradient tinh tế cho button/accent. Dark mode: dùng `#0f0f0f`–`#1a1a1a`, KHÔNG `#000`.
- Contrast ≥ 4.5:1 (WCAG AA).
- ❌ Không hardcode màu. Tối đa 3 màu chủ đạo (1 primary + 1-2 accent).

## 5. Effects & Depth — Hiệu ứng & Chiều sâu

- Shadow system nhất quán (xs → 2xl). Border radius system (sm:4px → full:9999px).
- Glassmorphism khi phù hợp: `backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.1)`.
- CTA: subtle glow `box-shadow: 0 0 20px rgba(primary, 0.3)`.
- ❌ Shadow không quá đậm. Không `border: 1px solid black`.

## 6. Animation & Micro-interactions — Hoạt ảnh & Tương tác vi mô

- **MỌI interactive element** PHẢI có hover state + transition mượt:
  ```css
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1),
              box-shadow 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  ```
- Button hover: `translateY(-2px)` + tăng shadow. Card hover: nâng lên + tăng shadow.
- Scroll animation: fade-in/slide-up khi vào viewport (IntersectionObserver).
- Hero: staggered fade-in khi load. `html { scroll-behavior: smooth; }`
- ❌ Không < 150ms (giật) hoặc > 800ms (lag). PHẢI respect `prefers-reduced-motion`:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
  ```

## 7. Responsive Design — Thiết kế đáp ứng

- **Mobile-first**: CSS mobile trước, `min-width` media query mở rộng.
- Breakpoints: 640px (tablet) → 1024px (laptop) → 1280px (desktop) → 1536px (wide).
- Fluid heading: `font-size: clamp(2rem, 5vw, 4.5rem)`.
- Touch target ≥ 44x44px. Ảnh: `max-width: 100%; height: auto`.
- ❌ KHÔNG BAO GIỜ scroll ngang trên mobile. Không pixel cố định cho layout width.

## 8. Component Patterns — Mẫu thành phần giao diện

- **Navbar**: Sticky, backdrop blur, logo trái, CTA phải. Mobile: hamburger menu mượt.
- **Hero**: ≥80vh, heading lớn (gradient text), sub-heading max-width 600-700px, CTA nổi bật.
- **Card**: radius 12-20px, padding 24-32px, hover nâng + shadow. Dark: bg sáng hơn nền.
- **Button**: padding 12px 28px, radius lg, font-weight 600, hover translateY(-2px).
- **Footer**: Multi-column, bg tối hơn body, spacing rõ ràng.

## 9. Image & Media — Hình ảnh & Đa phương tiện

- KHÔNG placeholder trống — dùng `generate_image` tool nếu cần ảnh.
- Nội dung thực hoặc mẫu có ý nghĩa, KHÔNG Lorem ipsum.
- `alt` text bắt buộc. `loading="lazy"` cho ảnh dưới fold.
- Icon: SVG inline hoặc icon font (Lucide, Phosphor). KHÔNG PNG cho icon.

## 10. Accessibility & SEO — Khả năng truy cập & Tối ưu công cụ tìm kiếm

- Semantic HTML: `<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`.
- Một `<h1>` duy nhất, hierarchy đúng. Focus state rõ ràng (KHÔNG `outline: none` thô).
- `aria-label` cho icon button. Form input PHẢI có `<label>`.
- Meta bắt buộc: `charset`, `viewport`, `title`, `description`, OG tags, favicon.
- Font: `font-display: swap` + preload. JS: `defer/async`. Set `width/height` cho img.

## 11. Code Structure — Cấu trúc mã nguồn

- CSS theo thứ tự: Reset → Tokens → Base → Layout → Components → Utilities → Animations → Responsive.
- Class: `kebab-case`. ID: `camelCase`. BEM cho component phức tạp.
- File structure: `index.html` + `css/` + `js/` + `assets/images,icons,fonts/`.
- ❌ Không CSS dư thừa, trùng lặp. Không hardcode giá trị — dùng CSS variable.

## 12. Checklist bắt buộc trước khi hoàn thành — Danh sách kiểm tra cuối cùng

- [ ] Premium, không generic? Color palette nhất quán qua CSS vars?
- [ ] Google Font + typography hierarchy? Spacing hệ 4px/8px?
- [ ] Mọi button/link/card có hover state + animation?
- [ ] Scroll animation + hero load animation? Smooth scrolling?
- [ ] Mobile 375px OK? Tablet 768px OK? Desktop 1440px OK? Không scroll ngang?
- [ ] Semantic HTML? Meta tags đầy đủ? Accessibility (alt, label, contrast, focus)?
- [ ] Font preload + swap? Lazy loading? `prefers-reduced-motion`?
