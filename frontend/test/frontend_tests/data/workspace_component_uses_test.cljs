;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns frontend-tests.data.workspace-component-uses-test
  (:require
   [app.common.transit :as transit]
   [app.common.uuid :as uuid]
   [app.main.data.workspace.component-uses :as cu]
   [cljs.test :as t :include-macros true]))

;; The shape maps below are the minimum that `make-component-instance` writes
;; on the instance heads: `:component-file` plus `:component-id` identify the
;; component, `:main-instance` marks the definition and the shapes inside a
;; copy only have `:shape-ref`.
;;
;; Every fixture holds two shapes on purpose: a main instance of the library
;; it counts, and an instance of another library that reuses the same
;; `:component-id`. So every count assertion breaks when the function counts
;; main instances, and also when it ignores `:component-file`.

(defn- main-shape
  [component-id component-file]
  {:id (uuid/next)
   :component-id component-id
   :component-file component-file
   :main-instance true})

(defn- instance-shape
  [component-id component-file]
  {:id (uuid/next)
   :component-id component-id
   :component-file component-file
   :shape-ref (uuid/next)})

(defn- nested-shape
  []
  {:id (uuid/next)
   :shape-ref (uuid/next)})

(defn- page
  [& shapes]
  {:id (uuid/next)
   :objects (into {} (map (fn [shape] [(:id shape) shape])) shapes)})

(defn- file-data
  [& pages]
  (let [pages-index (into {} (map (fn [page] [(:id page) page])) pages)]
    {:pages (vec (keys pages-index))
     :pages-index pages-index}))

(t/deftest mains-are-not-counted
  (t/testing "the main component is the definition, not a use of itself"
    (let [file-id      (uuid/next)
          library-id   (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (main-shape component-id file-id)
                                        (instance-shape component-id library-id)))]
      ;; The empty map only holds if the main of this file and the instance of
      ;; the other library are both skipped.
      (t/is (= {} (cu/count-uses data file-id)))))

  (t/testing "an instance of the file is counted, the main is not"
    (let [file-id      (uuid/next)
          library-id   (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (main-shape component-id file-id)
                                        (instance-shape component-id file-id)
                                        (instance-shape component-id library-id)))]
      (t/is (= {component-id 1} (cu/count-uses data file-id))))))

(t/deftest instances-on-several-pages-are-summed
  (t/testing "every page of the file contributes to the count"
    (let [file-id      (uuid/next)
          library-id   (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (main-shape component-id file-id)
                                        (instance-shape component-id file-id)
                                        (instance-shape component-id file-id)
                                        (instance-shape component-id library-id))
                                  (page (instance-shape component-id file-id)
                                        (nested-shape)))]
      (t/is (= {component-id 3} (cu/count-uses data file-id))))))

(t/deftest components-of-another-library-are-not-mixed-up
  (t/testing "the same component id on another library is a different component"
    (let [file-id      (uuid/next)
          library-id   (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (main-shape component-id file-id)
                                        (instance-shape component-id file-id)
                                        (main-shape component-id library-id)
                                        (instance-shape component-id library-id)
                                        (instance-shape component-id library-id)))]
      ;; The fixture keeps a main of both libraries, so each count also breaks
      ;; if main instances are counted.
      (t/is (= {component-id 1} (cu/count-uses data file-id)))
      (t/is (= {component-id 2} (cu/count-uses data library-id))))))

(t/deftest not-loaded-pages-return-unknown
  ;; These assertions guard the loading gate, not the counting rule: the file
  ;; data gives an unknown result, never a wrong count.
  (t/testing "a loaded page without instances of the library is a real zero"
    (let [file-id      (uuid/next)
          library-id   (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (main-shape component-id file-id)
                                        (instance-shape component-id library-id))
                                  (page))]
      ;; The first page holds shapes the count must skip, the second one is a
      ;; loaded page without shapes: the count is a real zero, not unknown.
      (t/is (= {} (cu/count-uses data file-id)))))

  (t/testing "a page still stored as a lazy pointer is unknown, not zero"
    (let [file-id      (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (instance-shape component-id file-id)))
          page-id      (first (:pages data))
          pointer      (transit/->Pointer (uuid/next) nil)
          lazy-data    (update data :pages-index assoc page-id pointer)]
      (t/is (nil? (cu/count-uses lazy-data file-id)))))

  (t/testing "a page listed on the file but not loaded is unknown, not zero"
    (let [file-id      (uuid/next)
          component-id (uuid/next)
          data         (file-data (page (instance-shape component-id file-id)))
          page-id      (first (:pages data))
          partial-data (update data :pages-index dissoc page-id)]
      (t/is (nil? (cu/count-uses partial-data file-id))))))
