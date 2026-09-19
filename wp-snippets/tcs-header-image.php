<?php
/**
 * TCS Header Image Adapter
 * ------------------------------------------------------------------
 * Version: 1.2.1
 * Canonical source: tcs-scripts/wp-snippets/tcs-header-image.php
 * Ticket: https://github.com/The-Canadian-Space/tcs-scripts/issues/8
 * Design spec: https://github.com/The-Canadian-Space/tcs-scripts/issues/5
 * Architecture: https://github.com/The-Canadian-Space/tcs-scripts/issues/1
 *
 * The WordPress side of the Blog Header Images V1 pipeline.
 * Reads _tcs_header_url post meta (written by the n8n Blog Posting workflow
 * after image-prep's POST /header generates the branded composite) and injects
 * that URL into every WordPress surface that would otherwise emit a featured
 * image, OG image, or RSS thumbnail:
 *
 *   - post_thumbnail_html                    → featured image on category /
 *                                              archive / list contexts.
 *                                              Suppressed on singular post view
 *                                              (v1.2.1 — reader sees the article
 *                                              body's natural imagery, no
 *                                              duplication with the composite).
 *   - rank_math/opengraph/facebook/image     → og:image
 *   - rank_math/opengraph/twitter/image      → twitter:image
 *   - wpseo_opengraph_image                  → Yoast forward-compat
 *   - the_content_feed / the_excerpt_rss     → RSS feed thumbnail
 *
 * When _tcs_header_url is missing, every hook returns the original value —
 * older posts and posts not yet through the new pipeline fall back to
 * WordPress-native featured images (or FIFU during the transition period).
 *
 * Retires the FIFU plugin: once every new post ships with a header URL,
 * FIFU can be deactivated + uninstalled.
 *
 * Deployment: Code Snippets plugin, scope "Run everywhere". This file's
 * canonical form lives in git — any edit made in the WP admin editor must be
 * exported back to the file in this repo and version-bumped.
 *
 * ------------------------------------------------------------------
 * WARNING: When pasting into Code Snippets, drop the opening `<?php` tag.
 * The plugin evals the snippet body directly.
 * ------------------------------------------------------------------
 *
 * CHANGELOG
 *   1.2.1 (2026-09-19):
 *     - Reverse the v1.2.0 approach for singular post view. Instead of
 *       stripping the first `<img>` from the body, suppress the branded
 *       composite (returned empty from `post_thumbnail_html`) whenever the
 *       reader is on `is_singular('post')`. Result: the singular reader sees
 *       the article's natural imagery, no composite duplicating it at the
 *       top. Category / archive contexts still receive the composite as their
 *       thumbnail. OG / Twitter previews still receive the composite. RSS
 *       still receives the composite prepended.
 *     - `the_content` filter from v1.2.0 removed.
 *     - RSS `the_content_feed` filter still strips the first body <img> so RSS
 *       readers don't see the duplicate (v1.2.0 behaviour preserved for RSS).
 *   1.2.0 (2026-09-19):
 *     - Strip the first `<img>` from article body via `the_content` when
 *       `_tcs_header_url` is set. Spotted on a live post 2026-09-19 where
 *       the branded composite + raw source image both appeared. Superseded
 *       by v1.2.1's inverse approach.
 *   1.1.0 (2026-09-17):
 *     - Register `_tcs_header_url` as REST-visible protected meta so the n8n
 *       Blog Posting workflow's POST /wp/v2/posts payload can actually store
 *       it. Without this, WordPress silently drops unregistered protected
 *       (underscore-prefixed) meta on save — which is exactly what happened
 *       to post 2420 on 2026-09-17.
 *     - Bump every filter priority from 10 → 999 so this snippet wins against
 *       FIFU (and anything else) on `post_thumbnail_html` and the OG image
 *       hooks. FIFU otherwise runs after us and can override our injection.
 *   1.0.0 (2026-09-17): initial release.
 */

// ------------------------------------------------------------------
// Meta key we read. Must match what the n8n Blog Posting workflow writes.
// ------------------------------------------------------------------
if (!defined('TCS_HEADER_META_KEY')) {
    define('TCS_HEADER_META_KEY', '_tcs_header_url');
}

// ------------------------------------------------------------------
// 0. Register `_tcs_header_url` for REST — without this, WP silently drops
//    it on POST /wp/v2/posts because underscore-prefixed meta is "protected"
//    and unregistered protected meta is rejected by WP REST since 4.9.8.
// ------------------------------------------------------------------
if (!function_exists('tcs_header_register_meta')) {
    function tcs_header_register_meta() {
        register_post_meta('post', TCS_HEADER_META_KEY, array(
            'show_in_rest'  => true,
            'type'          => 'string',
            'single'        => true,
            'auth_callback' => function () { return current_user_can('edit_posts'); },
        ));
    }
    add_action('init', 'tcs_header_register_meta');
}

/**
 * Return the TCS header URL for a post, or null if none set.
 * Callers use `if ($url === null) return $original` to fall through cleanly.
 */
if (!function_exists('tcs_header_get_url')) {
    function tcs_header_get_url($post_id) {
        if (empty($post_id)) {
            return null;
        }
        $url = get_post_meta((int) $post_id, TCS_HEADER_META_KEY, true);
        if (!is_string($url) || $url === '') {
            return null;
        }
        return $url;
    }
}

/**
 * Compose an <img> tag using our header URL. Preserves the alt text WP would
 * have used (post title) and any class attribute the theme passed.
 * Sets loading=lazy + decoding=async by default; theme-passed values win.
 */
if (!function_exists('tcs_header_render_img')) {
    function tcs_header_render_img($url, $post_id, $attr = array()) {
        if (!is_array($attr)) {
            $attr = array();
        }
        $alt = isset($attr['alt']) && $attr['alt'] !== '' ? $attr['alt'] : get_the_title($post_id);
        $class = 'wp-post-image tcs-header-image';
        if (!empty($attr['class'])) {
            $class .= ' ' . $attr['class'];
        }
        $loading = isset($attr['loading']) ? $attr['loading'] : 'lazy';
        $decoding = isset($attr['decoding']) ? $attr['decoding'] : 'async';

        return sprintf(
            '<img src="%s" alt="%s" class="%s" loading="%s" decoding="%s" />',
            esc_url($url),
            esc_attr($alt),
            esc_attr($class),
            esc_attr($loading),
            esc_attr($decoding)
        );
    }
}

// ------------------------------------------------------------------
// 1. Featured image on the page + REST title.rendered thumbnails
//    Priority 999 = we run last, after FIFU + any other thumbnail plugin.
//    On singular post view: return empty so the composite doesn't render
//    at the top of the article — the reader gets the natural article body
//    without the composite duplicating the first body image (v1.2.1).
//    Category / archive contexts still get the composite as the thumbnail.
// ------------------------------------------------------------------
if (!function_exists('tcs_header_filter_post_thumbnail_html')) {
    function tcs_header_filter_post_thumbnail_html($html, $post_id, $post_thumbnail_id, $size, $attr) {
        $url = tcs_header_get_url($post_id);
        if ($url === null) {
            return $html;
        }
        if (is_singular('post')) {
            return '';
        }
        return tcs_header_render_img($url, $post_id, is_array($attr) ? $attr : array());
    }
    add_filter('post_thumbnail_html', 'tcs_header_filter_post_thumbnail_html', 999, 5);
}

// ------------------------------------------------------------------
// 2. Open Graph image — Rank Math (primary SEO plugin on the site)
// ------------------------------------------------------------------
if (!function_exists('tcs_header_filter_rankmath_og_image')) {
    function tcs_header_filter_rankmath_og_image($image) {
        $post_id = (int) get_queried_object_id();
        $url = tcs_header_get_url($post_id);
        return $url !== null ? $url : $image;
    }
    add_filter('rank_math/opengraph/facebook/image', 'tcs_header_filter_rankmath_og_image', 999);
    add_filter('rank_math/opengraph/twitter/image',  'tcs_header_filter_rankmath_og_image', 999);
}

// ------------------------------------------------------------------
// 3. Open Graph image — Yoast (forward-compat if we ever swap SEO plugin)
// ------------------------------------------------------------------
if (!function_exists('tcs_header_filter_yoast_og_image')) {
    function tcs_header_filter_yoast_og_image($image) {
        $post_id = (int) get_queried_object_id();
        $url = tcs_header_get_url($post_id);
        return $url !== null ? $url : $image;
    }
    add_filter('wpseo_opengraph_image', 'tcs_header_filter_yoast_og_image', 999);
}

// ------------------------------------------------------------------
// 4. RSS feed thumbnail — prepend an <img> to the item content and excerpt,
//    AND strip the body's first image so RSS readers don't see the duplicate.
//    (RSS readers don't render WordPress' featured image, so we prepend
//    manually — but that means we still need to strip the first body img
//    to avoid the same duplication we suppress at the singular-view level.)
// ------------------------------------------------------------------
if (!function_exists('tcs_header_strip_first_img')) {
    function tcs_header_strip_first_img($content) {
        $content = preg_replace('/<img[^>]*>/i', '', $content, 1);
        $content = preg_replace('/<figure[^>]*>\s*<\/figure>/i', '', $content);
        $content = preg_replace('/^(\s*<p>\s*<\/p>\s*)+/i', '', $content);
        return $content;
    }
}

if (!function_exists('tcs_header_filter_rss_content')) {
    function tcs_header_filter_rss_content($content) {
        $post_id = (int) get_the_ID();
        $url = tcs_header_get_url($post_id);
        if ($url === null) {
            return $content;
        }
        $content = tcs_header_strip_first_img($content);
        $alt = get_the_title($post_id);
        $img = sprintf(
            '<figure class="tcs-header-rss"><img src="%s" alt="%s" /></figure>',
            esc_url($url),
            esc_attr($alt)
        );
        return $img . $content;
    }
    add_filter('the_content_feed', 'tcs_header_filter_rss_content', 999);
    add_filter('the_excerpt_rss',  'tcs_header_filter_rss_content', 999);
}
