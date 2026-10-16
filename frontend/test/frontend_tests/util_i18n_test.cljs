;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.util-i18n-test
  "Regression tests for locale resolution.

  The namespace fixture forces the browser language to \"ru\", so an assertion
  that expects another locale fails if the code falls back to the browser
  language."
  (:require
   [app.config :as cf]
   [app.util.globals :as globals]
   [app.util.i18n :as i18n]
   [app.util.modules :as mod]
   [app.util.storage :as storage]
   [cljs.test :as t :include-macros true]
   [cuerdas.core :as str]
   [promesa.core :as p]))

;; --- FIXTURES

(defn- install-russian-browser!
  "Force the browser language to \"ru\", so that a fallback to the browser
  language is observable."
  []
  (js/Object.defineProperty globals/navigator "language" #js {:value "ru" :configurable true}))

(defn- restore-browser!
  []
  (js-delete globals/navigator "language"))

;; A map fixture, not a function one: for an async test `:after` runs only once
;; the test calls `done`, so the override covers the whole async body. See
;; composable-tests/comp/sync-test.
(t/use-fixtures :each
  {:before install-russian-browser!
   :after  restore-browser!})

;; --- HELPERS

(defn- set-locale-stubbing-translations
  "Call `i18n/set-locale` for `lang` with the on-demand translation loading
  replaced by a fake module, so the call does not need the built translation
  files. `requested` receives the path of the module that was loaded."
  [lang requested]
  (with-redefs [mod/import (fn [path]
                             (reset! requested path)
                             (p/resolved #js {"default" #js {}}))]
    (i18n/set-locale lang)))

;; --- PURE RESOLUTION

(t/deftest resolve-locale-unset-language-uses-fallback
  (t/testing "an unset language resolves to the fallback, not the browser language"
    (t/is (= "ca" (i18n/resolve-locale nil "ca")))
    (t/is (= "ca" (i18n/resolve-locale "" "ca")))))

(t/deftest resolve-locale-explicit-language-is-kept
  (t/testing "an explicit supported language is used as-is"
    (t/is (= "uk" (i18n/resolve-locale "uk" "ca")))
    (t/is (= "en" (i18n/resolve-locale "en" "ca")))
    (t/is (= "ca" (i18n/resolve-locale "ca" "en")))))

(t/deftest resolve-locale-normalizes-case-and-region
  (t/testing "case and region separators are normalized against supported locales"
    (t/is (= "pt_br" (i18n/resolve-locale "pt-BR" "ca")))
    (t/is (= "pt_pt" (i18n/resolve-locale "pt-PT" "ca")))
    (t/is (= "fr_ca" (i18n/resolve-locale "fr-CA" "ca")))))

(t/deftest resolve-locale-unsupported-language-uses-fallback
  (t/testing "an unsupported language resolves to the fallback"
    (t/is (= "ca" (i18n/resolve-locale "xx-YY" "ca")))
    (t/is (= "ca" (i18n/resolve-locale "  " "ca")))))

;; --- CURRENT LOCALE

(t/deftest get-current-ignores-persisted-browser-locale
  (t/testing "a locale persisted by an older browser autodetect does not win"
    (let [key  :app.util.i18n/locale
          prev (get @storage/global key)]
      (try
        (swap! storage/global assoc key "ru")
        (t/is (= cf/default-language (i18n/get-current)))
        (finally
          (if (nil? prev)
            (swap! storage/global dissoc key)
            (swap! storage/global assoc key prev)))))))

;; --- SET-LOCALE

(t/deftest set-locale-nil-loads-default-language-not-browser-language
  (t/async done
    (let [requested (atom nil)]
      (-> (set-locale-stubbing-translations nil requested)
          (.then (fn [_]
                   (t/is (= cf/default-language @i18n/locale))
                   (t/is (= cf/default-language i18n/*current-locale*))
                   (t/is (str/includes? @requested (str "translation." cf/default-language ".js")))
                   (done)))
          (.catch (fn [err]
                    (t/is false (str "unexpected error: " err))
                    (done)))))))

(t/deftest set-locale-empty-loads-default-language-not-browser-language
  (t/async done
    (let [requested (atom nil)]
      (-> (set-locale-stubbing-translations "" requested)
          (.then (fn [_]
                   (t/is (= cf/default-language @i18n/locale))
                   (t/is (= cf/default-language i18n/*current-locale*))
                   (t/is (str/includes? @requested (str "translation." cf/default-language ".js")))
                   (done)))
          (.catch (fn [err]
                    (t/is false (str "unexpected error: " err))
                    (done)))))))

(t/deftest set-locale-explicit-language-is-applied
  (t/async done
    (let [requested (atom nil)]
      (-> (set-locale-stubbing-translations "uk" requested)
          (.then (fn [_]
                   (t/is (= "uk" @i18n/locale))
                   (t/is (= "uk" i18n/*current-locale*))
                   (t/is (str/includes? @requested "translation.uk.js"))
                   ;; Leave the shared locale state on the default language for
                   ;; the rest of the test run.
                   (set-locale-stubbing-translations nil requested)))
          (.then (fn [_] (done)))
          (.catch (fn [err]
                    (t/is false (str "unexpected error: " err))
                    (done)))))))
