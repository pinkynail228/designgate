;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.data.workspace-shortcuts-figma-displace-test
  (:require
   [app.main.data.shortcuts :as ds]
   [app.main.data.workspace.shortcuts :as shortcuts]
   [cljs.test :as t :include-macros true]))

;; A Figma key must never trigger a different action. These are the
;; keys that used to displace a Figma binding: each one is removed from
;; the action that had it and, where Figma uses it, given to the action
;; Figma binds it to. Command strings go through `ds/c-mod`/`ds/ca-mod`
;; so the expectations resolve the same way on macOS and on
;; Windows/Linux.

(defn- index
  []
  (ds/build-command-index shortcuts/shortcuts))

(defn- tooltip
  [id]
  (:tooltip (get shortcuts/shortcuts id)))

(t/deftest figma-keys-do-not-displace-other-actions
  (let [idx (index)]
    (t/testing "Shift+G is Figma's layout guides, not ungroup"
      (t/is (nil? (get idx "shift+g")))
      (t/is (= :ungroup (get idx (ds/c-mod "shift+g")))))
    (t/testing "M+K is Figma's actions menu, not create variant"
      (t/is (and (= :create-component-variant (get idx (ds/ca-mod "k")))
                 (nil? (get idx (ds/c-mod "k"))))))
    (t/testing "M+Shift+K is Figma's place image, not detach component"
      (t/is (and (= :insert-image (get idx (ds/c-mod "shift+k")))
                 (= :detach-component (get idx (ds/ca-mod "b"))))))
    (t/testing "M+Shift+R is Figma's paste to replace, not toggle rulers"
      (t/is (nil? (get idx (ds/c-mod "shift+r")))))
    (t/testing "backslash selects the parent layer; M+backslash hides the UI"
      (t/is (= :select-parent-layer (get idx "\\")))
      (t/is (= :hide-ui (get idx (ds/c-mod "\\")))))))

(t/deftest figma-tooltips-match-their-keys
  (t/testing "the retargeted keys show their new key"
    (t/is (= (ds/meta-shift "G") (tooltip :ungroup)))
    (t/is (= (ds/meta-shift "K") (tooltip :insert-image)))
    (t/is (= (ds/meta "\\") (tooltip :hide-ui)))))

(t/deftest insert-image-keeps-the-existing-key
  (t/testing "the Figma key joins the existing key instead of replacing it"
    (t/is (= [(ds/c-mod "shift+k") "shift+k"]
             (:command (get shortcuts/shortcuts :insert-image))))))

(t/deftest displaced-actions-keep-their-toolbar-entry
  (doseq [id [:toggle-rulers :toggle-alignment :toggle-snap-ruler-guide]]
    (t/testing (str id)
      (t/is (and (contains? shortcuts/shortcuts id)
                 (= [] (:command (get shortcuts/shortcuts id)))
                 (nil? (tooltip id)))
            (str id " stays in the table with no default key")))))
