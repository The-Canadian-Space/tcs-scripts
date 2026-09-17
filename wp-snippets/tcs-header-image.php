<?php
/**
 * TCS Header Image Adapter
 * ------------------------------------------------------------------
 * Version: 1.0.0
 * Canonical source: tcs-scripts/wp-snippets/tcs-header-image.php
 * Ticket: https://github.com/The-Canadian-Space/tcs-scripts/issues/8
 * Design spec: https://github.com/The-Canadian-Space/tcs-scripts/issues/5
 * Architecture: https://github.com/The-Canadian-Space/tcs-scripts/issues/1
 *
 * The WordPress side of the Blog Header Images V1 pipeline.
 * Reads _tcs_header_url post meta (written by the n8n Blog Posting workflow
 * after image-prep's POST /header generates the branded 800px composite) and
 * injects that URL into every WordPress surface that would otherwise emit a
 * featured image, OG image, or RSS thumbnail:
 *
 *   - post_thumbnail_html                    → in-page featured image
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
 */

// ------------------------------------------------------------------
// Meta key we read. Must match what the n8n Blog Posting workflow writes.
// ------------------------------------------------------------------
if (!defined('TCS_HEADER_META_KEY')) {
    define('TCS_HEADER_META_KEY', '_tcs_header_url');
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
// ------------------------------------------------------------------
if (!function_exists('tcs_header_filter_post_thumbnail_html')) {
    function tcs_header_filter_post_thumbnail_html($html, $post_id, $post_thumbnail_id, $size, $attr) {
        $url = tcs_header_get_url($post_id);
        if ($url === null) {
            return $html;
        }
        return tcs_header_render_img($url, $post_id, is_array($attr) ? $attr : array());
    }
    add_filter('post_thumbnail_html', 'tcs_header_filter_post_thumbnail_html', 10, 5);
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
    add_filter('rank_math/opengraph/facebook/image', 'tcs_header_filter_rankmath_og_image', 10);
    add_filter('rank_math/opengraph/twitter/image',  'tcs_header_filter_rankmath_og_image', 10);
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
    add_filter('wpseo_opengraph_image', 'tcs_header_filter_yoast_og_image', 10);
}

// ------------------------------------------------------------------
// 4. RSS feed thumbnail — prepend an <img> to the item content and excerpt
// ------------------------------------------------------------------
if (!function_exists('tcs_header_filter_rss_content')) {
    function tcs_header_filter_rss_content($content) {
        $post_id = (int) get_the_ID();
        $url = tcs_header_get_url($post_id);
        if ($url === null) {
            return $content;
        }
        $alt = get_the_title($post_id);
        $img = sprintf(
            '<figure class="tcs-header-rss"><img src="%s" alt="%s" /></figure>',
            esc_url($url),
            esc_attr($alt)
        );
        return $img . $content;
    }
    add_filter('the_content_feed', 'tcs_header_filter_rss_content', 10);
    add_filter('the_excerpt_rss',  'tcs_header_filter_rss_content', 10);
}
