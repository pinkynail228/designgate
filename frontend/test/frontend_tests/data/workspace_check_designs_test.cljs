;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns frontend-tests.data.workspace-check-designs-test
  (:require
   [app.common.uuid :as uuid]
   [app.main.data.workspace.check-designs :as dwcd]
   [cljs.test :as t :include-macros true]))

;; The check reports bindings, so every fixture below pins a value that could
;; be reported and the binding (color reference, library typography or applied
;; token) that must keep it out of the findings.

(def ^:private hard-coded-fill
  {:fill-color "#ff0000" :fill-opacity 1})

(def ^:private bound-fill
  {:fill-color "#ff0000"
   :fill-opacity 1
   :fill-color-ref-id uuid/zero
   :fill-color-ref-file uuid/zero})

(def ^:private hard-coded-gradient
  {:fill-color "#ff0000"
   :fill-opacity 1
   :fill-color-gradient {:type :linear
                         :start-x 0
                         :start-y 0
                         :end-x 1
                         :end-y 0
                         :stops [{:color "#ff0000" :opacity 1 :offset 0}
                                 {:color "#0000ff" :opacity 1 :offset 1}]}})

(def ^:private hard-coded-image
  {:fill-color "#ff0000"
   :fill-opacity 1
   :fill-image {:id uuid/zero
                :name "image"
                :width 10
                :height 10
                :mtype "image/png"
                :keep-aspect-ratio true}})

(def ^:private hard-coded-stroke-gradient
  {:stroke-color "#ff0000"
   :stroke-opacity 1
   :stroke-width 1
   :stroke-color-gradient {:type :linear
                           :start-x 0
                           :start-y 0
                           :end-x 1
                           :end-y 0
                           :stops [{:color "#ff0000" :opacity 1 :offset 0}
                                   {:color "#0000ff" :opacity 1 :offset 1}]}})

(def ^:private hard-coded-stroke-image
  {:stroke-color "#ff0000"
   :stroke-opacity 1
   :stroke-width 1
   :stroke-image {:id uuid/zero
                  :name "image"
                  :width 10
                  :height 10
                  :mtype "image/png"
                  :keep-aspect-ratio true}})

(defn- page-objects
  "Build a page objects map with the given shapes as children of the page
   root, in the same order."
  [shapes]
  (let [objects (into {} (map (juxt :id identity)) shapes)]
    (assoc objects uuid/zero
           {:id uuid/zero
            :name "Page"
            :type :frame
            :shapes (mapv :id shapes)})))

(defn- rect
  [id name & {:keys [fills strokes hidden applied-tokens]}]
  (cond-> {:id id :name name :type :rect}
    (some? fills)          (assoc :fills fills)
    (some? strokes)        (assoc :strokes strokes)
    (true? hidden)         (assoc :hidden true)
    (some? applied-tokens) (assoc :applied-tokens applied-tokens)))

(defn- span
  "A leaf text node. It has no :type, as Penpot stores text nodes."
  [& {:keys [text typography-ref-id fills]}]
  (cond-> {:text (or text "Lorem ipsum")}
    (some? typography-ref-id) (assoc :typography-ref-id typography-ref-id)
    (some? fills)             (assoc :fills fills)))

(defn- paragraph
  [& spans]
  {:type "paragraph" :children (vec spans)})

(defn- content
  [& paragraphs]
  {:type "root"
   :children [{:type "paragraph-set" :children (vec paragraphs)}]})

(defn- text
  [id name content & {:keys [applied-tokens hidden]}]
  (cond-> {:id id :name name :type :text :content content}
    (some? applied-tokens) (assoc :applied-tokens applied-tokens)
    (true? hidden)         (assoc :hidden true)))

(t/deftest bound-fill-not-reported
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1" :fills [bound-fill])])]
    (t/testing "a fill with a library color reference is not hard-coded"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest unbound-solid-fill-reported
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1" :fills [hard-coded-fill])])]
    (t/testing "a solid fill with no reference is hard-coded"
      (t/is (= [{:shape-id id :shape-name "Rect 1" :kind :fill-color}]
               (dwcd/check-page objects))))))

(t/deftest token-bound-fill-not-reported
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1"
                                     :fills [hard-coded-fill]
                                     :applied-tokens {:fill "brand.primary"})])]
    (t/testing "a fill bound to a color token is not hard-coded"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest hidden-shapes-skipped
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1" :fills [hard-coded-fill] :hidden true)])]
    (t/testing "hidden shapes are skipped"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest image-and-gradient-fills-not-reported
  (let [gradient-id (uuid/random)
        image-id    (uuid/random)
        objects     (page-objects [(rect gradient-id "Rect 1" :fills [hard-coded-gradient])
                                   (rect image-id "Rect 2" :fills [hard-coded-image])])]
    (t/testing "a gradient or an image wins over its fallback color"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest gradient-stroke-not-reported
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1" :strokes [hard-coded-stroke-gradient])])]
    (t/testing "a gradient stroke wins over its fallback color"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest image-stroke-not-reported
  (let [id      (uuid/random)
        objects (page-objects [(rect id "Rect 1" :strokes [hard-coded-stroke-image])])]
    (t/testing "an image stroke wins over its fallback color"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest hard-coded-typography-reported
  (let [hard-id  (uuid/random)
        ref-id   (uuid/random)
        token-id (uuid/random)
        objects  (page-objects
                  [(text hard-id "Text 1" (content (paragraph (span))))
                   (text ref-id "Text 2" (content (paragraph (span :typography-ref-id uuid/zero))))
                   (text token-id "Text 3" (content (paragraph (span)))
                         :applied-tokens {:typography "title"})])]
    (t/testing "only the text with no typography binding is hard-coded"
      (t/is (= [{:shape-id hard-id :shape-name "Text 1" :kind :typography}]
               (dwcd/check-page objects))))))

(t/deftest mixed-typography-fragments-reported
  (let [id      (uuid/random)
        objects (page-objects
                 [(text id "Text 1"
                        (content (paragraph (span :text "Bound" :typography-ref-id uuid/zero)
                                            (span :text "Hard-coded"))))])]
    (t/testing "a fragment with no library typography is reported even when the text has a bound one"
      (t/is (= [{:shape-id id :shape-name "Text 1" :kind :typography}]
               (dwcd/check-page objects))))))

(t/deftest text-fill-with-ref-not-reported
  (let [id      (uuid/random)
        objects (page-objects
                 [(text id "Text 1"
                        (content (paragraph (span :typography-ref-id uuid/zero
                                                  :fills [bound-fill]))))])]
    (t/testing "a text fill with a library color reference is not hard-coded"
      (t/is (empty? (dwcd/check-page objects))))))

(t/deftest text-hard-coded-fill-reported
  (let [id      (uuid/random)
        objects (page-objects
                 [(text id "Text 1"
                        (content (paragraph (span :typography-ref-id uuid/zero
                                                  :fills [hard-coded-fill]))))])]
    (t/testing "a text fill with no reference is hard-coded, even with a library typography"
      (t/is (= [{:shape-id id :shape-name "Text 1" :kind :fill-color}]
               (dwcd/check-page objects))))))

(t/deftest text-token-bound-fill-not-reported
  (let [id      (uuid/random)
        objects (page-objects
                 [(text id "Text 1"
                        (content (paragraph (span :typography-ref-id uuid/zero
                                                  :fills [hard-coded-fill])))
                        :applied-tokens {:fill "brand.primary"})])]
    (t/testing "a text fill bound to a color token is not hard-coded"
      (t/is (empty? (dwcd/check-page objects))))))
