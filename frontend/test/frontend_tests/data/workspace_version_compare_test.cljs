;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns frontend-tests.data.workspace-version-compare-test
  (:require
   [app.common.types.tokens-lib :as ctob]
   [app.common.uuid :as uuid]
   [app.main.data.workspace.version-compare :as dwvc]
   [cljs.test :as t :include-macros true]
   [potok.v2.core :as ptk]))

;; HELPERS

(defn- component
  [id name path & {:as attrs}]
  (merge {:id id
          :name name
          :path path
          :main-instance-id (uuid/next)
          :main-instance-page (uuid/next)}
         attrs))

(defn- color
  [id name path value]
  {:id id
   :name name
   :path path
   :color value})

(defn- typography
  [id name path]
  {:id id
   :name name
   :path path
   :font-id "sourcesanspro"
   :font-family "sourcesanspro"
   :font-variant-id "regular"
   :font-size "14"
   :font-weight "480"
   :font-style "normal"
   :line-height "1.2"
   :letter-spacing "0"
   :text-transform "none"})

(defn- token
  [id name type value]
  (ctob/make-token :id id :name name :type type :value value))

(defn- tokens-lib
  [tokens]
  (let [set-id (uuid/next)
        lib    (-> (ctob/make-tokens-lib)
                   (ctob/add-set (ctob/make-token-set :id set-id :name "core")))]
    (reduce (fn [lib token] (ctob/add-token lib set-id token)) lib tokens)))

;; TESTS

(t/deftest diff-components
  (let [added-a  (uuid/next)
        added-b  (uuid/next)
        changed  (uuid/next)
        removed  (uuid/next)
        deleted  (uuid/next)
        gone     (uuid/next)
        old-data {:components {changed (component changed "Changed" "B")
                               removed (component removed "Removed" "A")
                               deleted (component deleted "Deleted" "A")
                               gone    (component gone "Gone" "A" :deleted true)}}
        new-data {:components {added-a (component added-a "Beta" "A")
                               added-b (component added-b "Alpha" "A")
                               changed (component changed "Changed" "B"
                                                  :main-instance-id (uuid/next))
                               deleted (component deleted "Deleted" "A"
                                                  :deleted true)
                               gone    (component gone "Gone" "A" :deleted true)}}
        result   (dwvc/diff-assets old-data new-data)]

    (t/testing "components added, sorted by path and name"
      (t/is (= [{:id added-b :name "Alpha" :path "A"}
                {:id added-a :name "Beta" :path "A"}]
               (get-in result [:components :added]))))

    (t/testing "components changed by a non volatile value"
      (t/is (= [{:id changed :name "Changed" :path "B"}]
               (get-in result [:components :changed]))))

    (t/testing "components removed, including ones just marked as deleted"
      (t/is (= [{:id deleted :name "Deleted" :path "A"}
                {:id removed :name "Removed" :path "A"}]
               (get-in result [:components :removed]))))))

(t/deftest diff-colors-and-typographies
  (let [color-added    (uuid/next)
        color-changed  (uuid/next)
        color-removed  (uuid/next)
        typo-added     (uuid/next)
        typo-changed   (uuid/next)
        typo-removed   (uuid/next)
        old-data {:colors {color-changed (color color-changed "Brand" "Tokens" "#000000")
                           color-removed (color color-removed "Old" "Tokens" "#111111")}
                  :typographies {typo-changed (typography typo-changed "Heading" "Text")
                                 typo-removed (typography typo-removed "Obsolete" "Text")}}
        new-data {:colors {color-added (color color-added "Accent" "Tokens" "#00ff00")
                           color-changed (assoc (color color-changed "Brand" "Tokens" "#ffffff")
                                                :modified-at 5)}
                  :typographies {typo-added (typography typo-added "Body" "Text")
                                 typo-changed (assoc (typography typo-changed "Heading" "Text")
                                                     :font-size "16")}}
        result   (dwvc/diff-assets old-data new-data)]

    (t/is (= [{:id color-added :name "Accent" :path "Tokens"}]
             (get-in result [:colors :added])))
    (t/is (= [{:id color-changed :name "Brand" :path "Tokens"}]
             (get-in result [:colors :changed])))
    (t/is (= [{:id color-removed :name "Old" :path "Tokens"}]
             (get-in result [:colors :removed])))

    (t/is (= [{:id typo-added :name "Body" :path "Text"}]
             (get-in result [:typographies :added])))
    (t/is (= [{:id typo-changed :name "Heading" :path "Text"}]
             (get-in result [:typographies :changed])))
    (t/is (= [{:id typo-removed :name "Obsolete" :path "Text"}]
             (get-in result [:typographies :removed])))))

(t/deftest diff-ignores-changes-on-volatile-keys-only
  ;; Every assertion holds a real change next to the volatile-only ones, so
  ;; an empty diff makes all of them fail.
  (let [component-changed  (uuid/next)
        component-volatile (uuid/next)
        component-added    (uuid/next)
        component-removed  (uuid/next)
        color-changed      (uuid/next)
        color-volatile     (uuid/next)
        typo-changed       (uuid/next)
        typo-volatile      (uuid/next)
        instance-id        (uuid/next)
        page-id            (uuid/next)
        old-data
        {:components
         {component-changed  (component component-changed "Button" "UI"
                                        :main-instance-id instance-id)
          component-volatile (component component-volatile "Card" "UI"
                                        :main-instance-id instance-id
                                        :main-instance-page page-id
                                        :modified-at 1
                                        :objects {"s1" {:id "s1" :modified-at 1}})
          component-removed  (component component-removed "Chart" "UI"
                                        :main-instance-id instance-id)}
         :colors
         {color-changed  (color color-changed "Brand" "Tokens" "#000000")
          color-volatile (assoc (color color-volatile "Accent" "Tokens" "#00ff00")
                                :modified-at 1)}
         :typographies
         {typo-changed  (typography typo-changed "Heading" "Text")
          typo-volatile (assoc (typography typo-volatile "Caption" "Text")
                               :modified-at 1)}}
        new-data
        {:components
         {component-changed  (component component-changed "Button" "UI"
                                        :main-instance-id (uuid/next))
          component-volatile (component component-volatile "Card" "UI"
                                        :main-instance-id instance-id
                                        :main-instance-page page-id
                                        :modified-at 2
                                        :objects {"s1" {:id "s1" :modified-at 7}})
          component-added    (component component-added "Badge" "UI"
                                        :main-instance-id instance-id)}
         :colors
         {color-changed  (color color-changed "Brand" "Tokens" "#ffffff")
          color-volatile (assoc (color color-volatile "Accent" "Tokens" "#00ff00")
                                :modified-at 2)}
         :typographies
         {typo-changed  (assoc (typography typo-changed "Heading" "Text") :font-size "16")
          typo-volatile (assoc (typography typo-volatile "Caption" "Text")
                               :modified-at 2)}}
        result (dwvc/diff-assets old-data new-data)]

    (t/testing "the volatile-only component is not reported, the real change is"
      (t/is (= [{:id component-changed :name "Button" :path "UI"}]
               (get-in result [:components :changed]))))

    (t/testing "the volatile-only component is not added or removed"
      (t/is (= [{:id component-added :name "Badge" :path "UI"}]
               (get-in result [:components :added])))
      (t/is (= [{:id component-removed :name "Chart" :path "UI"}]
               (get-in result [:components :removed]))))

    (t/testing "same for colors and typographies"
      (t/is (= [{:id color-changed :name "Brand" :path "Tokens"}]
               (get-in result [:colors :changed])))
      (t/is (= [{:id typo-changed :name "Heading" :path "Text"}]
               (get-in result [:typographies :changed]))))))

(t/deftest diff-tokens
  (let [changed-id     (uuid/next)
        type-changed-id (uuid/next)
        removed-id     (uuid/next)
        added-id       (uuid/next)
        old-data {:tokens-lib (tokens-lib [(token changed-id "colors.primary" :color "#000000")
                                           (token type-changed-id "radius.sm" :border-radius 4)
                                           (token removed-id "text.size" :dimensions 12)])}
        new-data {:tokens-lib (tokens-lib [(token changed-id "colors.primary" :color "#ffffff")
                                           (token type-changed-id "radius.sm" :spacing 4)
                                           (token added-id "spacing.sm" :spacing 8)])}
        result   (dwvc/diff-assets old-data new-data)]

    (t/is (= [{:id added-id :name "spacing.sm" :path ""}]
             (get-in result [:tokens :added])))

    (t/testing "changed by value or type, sorted by name"
      (t/is (= [{:id changed-id :name "colors.primary" :path ""}
                {:id type-changed-id :name "radius.sm" :path ""}]
               (get-in result [:tokens :changed]))))

    (t/is (= [{:id removed-id :name "text.size" :path ""}]
             (get-in result [:tokens :removed])))))

;; THE ANSWER OF A COMPARE REQUEST

(defn- store-answer
  "Applies the (private) answer event of the compare request identified by
  `token`, carrying the resolved `version-data` of the saved version."
  [state token file-id version-data]
  (ptk/update (#'dwvc/store-compare-diff token file-id version-data) state))

(defn- fail-answer
  "Applies the (private) failure of the compare request identified by
  `token`."
  [state token]
  (ptk/update (#'dwvc/fail-compare token) state))

(defn- compare-file
  "State with `file-id` as current file and a compare request `token` in
  flight."
  [file-id current-data token]
  {:current-file-id file-id
   :files {file-id {:data current-data}}
   :workspace-versions {:status :loaded
                        :data [{:id (uuid/next)}]
                        :compare {:status :loading :token token}}})

(t/deftest compare-answer-stores-only-the-diff
  (let [file-id        (uuid/next)
        token          (uuid/next)
        component-id   (uuid/next)
        version-data   {:components {component-id (component component-id "Button" "UI"
                                                             :main-instance-id (uuid/next))}}
        current-data   {:components {component-id (component component-id "Button" "UI"
                                                             :main-instance-id (uuid/next))}
                        :colors {}}
        state          (compare-file file-id current-data token)
        result         (store-answer state token file-id version-data)
        expected-diff  {:components {:added []
                                     :changed [{:id component-id :name "Button" :path "UI"}]
                                     :removed []}
                        :colors {:added [] :changed [] :removed []}
                        :typographies {:added [] :changed [] :removed []}
                        :tokens {:added [] :changed [] :removed []}}]

    (t/testing "the answer is stored, and only under the compare key"
      (t/is (= (assoc-in state [:workspace-versions :compare]
                         {:status :loaded :data expected-diff})
               result)))))

(t/deftest compare-answer-after-close-is-dropped
  (let [file-id      (uuid/next)
        token        (uuid/next)
        version-data {:components {}}
        state        (compare-file file-id {:components {}} token)
        closed       (ptk/update (dwvc/clear-compare) state)
        result       (store-answer closed token file-id version-data)]

    (t/testing "a late answer does not reopen the panel"
      (t/is (= closed result)))
    (t/is (nil? (get-in result [:workspace-versions :compare])))))

(t/deftest compare-answer-after-a-newer-request-is-dropped
  (let [file-id      (uuid/next)
        token-a      (uuid/next)
        token-b      (uuid/next)
        version-data {:components {}}
        state        (compare-file file-id {:components {}} token-b)
        result       (store-answer state token-a file-id version-data)]

    (t/testing "the newer request keeps its loading state"
      (t/is (= (:workspace-versions state) (:workspace-versions result))))))

(t/deftest compare-answer-for-another-file-closes-the-panel
  (let [file-id      (uuid/next)
        other-file   (uuid/next)
        token        (uuid/next)
        version-data {:components {}}
        state        (compare-file file-id {:components {}} token)
        switched     (assoc state :current-file-id other-file)
        result       (store-answer switched token file-id version-data)]

    (t/testing "the snapshot is not compared with the data of another file"
      (t/is (nil? (get-in result [:workspace-versions :compare])))
      (t/is (= other-file (:current-file-id result))))))

(t/deftest compare-failure-only-applies-to-the-request-in-flight
  (let [file-id (uuid/next)
        token   (uuid/next)
        state   (compare-file file-id {:components {}} token)]

    (t/testing "an in flight request is marked as failed"
      (t/is (= {:status :error}
               (get-in (fail-answer state token) [:workspace-versions :compare]))))

    (t/testing "a late failure does not reopen or change the panel"
      (let [closed (ptk/update (dwvc/clear-compare) state)]
        (t/is (= closed (fail-answer closed token)))))))
