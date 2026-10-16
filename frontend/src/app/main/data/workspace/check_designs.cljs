;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns app.main.data.workspace.check-designs
  "Pure logic of the Check designs panel: it reports the shapes of a page that
   use hard-coded values where a library asset or a design token could be used.

   The check is about bindings, not about values: a solid color is considered
   hard-coded when the paint has neither a color reference (a library color)
   nor a token applied for that attribute. Image and gradient paints are out of
   scope, and the renderer gives them priority over a fallback color, so they
   are skipped. Text paints and typographies live in the text content nodes, so
   every text fragment is inspected on its own. Shapes are visited in page
   order, hidden shapes are skipped and the function has no side effects."
  (:require
   [app.common.files.helpers :as cfh]
   [app.common.types.text :as txt]
   [app.common.types.token :as cto]
   [app.common.uuid :as uuid]))

(defn- token-bound?
  [shape token-attr]
  (contains? (:applied-tokens shape) token-attr))

(defn- solid-fill?
  "A fill is a hard-coded solid color when it carries a plain color, no library
   color reference and no gradient or image."
  [fill]
  (and (some? (:fill-color fill))
       (nil? (:fill-color-ref-id fill))
       (nil? (:fill-color-gradient fill))
       (nil? (:fill-image fill))))

(defn- solid-stroke?
  "A stroke is a hard-coded solid color when it is visible, carries a plain
   color, no library color reference and no gradient or image."
  [stroke]
  (and (not (:hidden stroke))
       (not= :none (:stroke-style stroke))
       (some? (:stroke-color stroke))
       (nil? (:stroke-color-ref-id stroke))
       (nil? (:stroke-color-gradient stroke))
       (nil? (:stroke-image stroke))))

(defn- content-nodes
  "All the nodes of the text content of a shape (nil for other shapes)."
  [shape]
  (when (cfh/text-shape? shape)
    (txt/node-seq (:content shape))))

(defn- typography-fragments
  "One entry per text node of the content: true when the fragment is bound to a
   library typography, either on the node itself or on any of its ancestors,
   false when it is not bound anywhere."
  [shape]
  (letfn [(walk [node inherited?]
            (let [bound? (or inherited? (some? (:typography-ref-id node)))]
              (if (txt/is-text-node? node)
                (list bound?)
                (mapcat #(walk % bound?) (:children node)))))]
    (when (cfh/text-shape? shape)
      (walk (:content shape) false))))

(defn- hard-coded-fill?
  "True when the shape or any of its text nodes has a hard-coded solid fill and
   no fill token applied. Text fills live in the content nodes, not in the
   shape."
  [shape]
  (and (not (token-bound? shape :fill))
       (or (some solid-fill? (:fills shape))
           (some #(some solid-fill? (:fills %)) (content-nodes shape)))))

(defn- hard-coded-stroke-color?
  "True when the shape has a hard-coded solid stroke color and no stroke color
   token applied."
  [shape]
  (and (not (token-bound? shape :stroke-color))
       (some solid-stroke? (:strokes shape))))

(defn- hard-coded-typography?
  "True when the shape is a text, no typography token is applied and any of its
   text fragments is not bound to a library typography."
  [shape]
  (and (cfh/text-shape? shape)
       (not (some #(token-bound? shape %) cto/typography-keys))
       (some false? (typography-fragments shape))))

(defn- shape-findings
  [shape]
  (let [base {:shape-id (:id shape)
              :shape-name (:name shape)}]
    (cond-> []
      (hard-coded-fill? shape)
      (conj (assoc base :kind :fill-color))

      (hard-coded-stroke-color? shape)
      (conj (assoc base :kind :stroke-color))

      (hard-coded-typography? shape)
      (conj (assoc base :kind :typography)))))

(defn- page-shapes
  "Shapes of a page in page order (a depth-first walk of the :shapes tree).
   Hidden shapes and their descendants are skipped."
  [objects]
  (letfn [(walk [id]
            (let [shape (get objects id)]
              (when (and (some? shape) (not (:hidden shape)))
                (cons shape (mapcat walk (:shapes shape))))))]
    (mapcat walk (:shapes (get objects uuid/zero)))))

(defn check-page
  "Given the objects of a page (a map of id -> shape), return a vector of
   findings, in page order, with the shapes that use hard-coded values:

     {:shape-id <uuid> :shape-name <string> :kind <keyword>}

   where kind is one of :fill-color, :stroke-color or :typography. A shape may
   produce more than one finding, at most one per kind."
  [objects]
  (into [] (mapcat shape-findings) (page-shapes objects)))
