import XCTest
final class LaunchTests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }
    @MainActor func testLongSongsListCentersCurrentAndManualBrowsingSuspendsFollowing() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_ROW_PLAYBACK"] = "1"
        app.launchEnvironment["ARRIFY_UI_LONG_LIST"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Songs"].firstMatch.tap()
        let current = app.buttons["songRow-75"]
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: current)
        waitForExpectations(timeout: 10)
        XCTAssertLessThan(abs(current.frame.midY - app.frame.midY), app.frame.height * 0.25)
        XCTAssertFalse(app.buttons["Earlier items"].exists)
        XCTAssertFalse(app.buttons["Show playing song"].exists)
        // Song 1 is outside the initial window around song 75. Reaching it
        // requires automatic previous-page loading as well as actual scrolling.
        let listX = current.frame.midX / app.frame.width
        let first = app.buttons["songRow-1"]
        for _ in 0..<24 {
            if first.isHittable { break }
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: listX, dy: 0.34))
            let end = app.coordinate(withNormalizedOffset: CGVector(dx: listX, dy: 0.72))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        XCTAssertTrue(first.isHittable, "Scrolling upward must load songs before the initial window")
        XCTAssertFalse(current.isHittable, "Manual browsing must not recenter on the playing song")
        XCTAssertFalse(app.buttons["Show playing song"].exists, "Songs must not reserve space for a return bar")
        app.buttons["Next song"].firstMatch.tap()
        expectation(for: NSPredicate(format: "label CONTAINS %@", "Song 110"), evaluatedWith: app.buttons["expandPlayer"])
        waitForExpectations(timeout: 10)
        XCTAssertTrue(first.isHittable, "Changing tracks after manual scrolling must preserve the browsing position")
        app.buttons["Home"].firstMatch.tap()
        app.buttons["Songs"].firstMatch.tap()
        let next = app.buttons["songRow-110"]
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: next)
        waitForExpectations(timeout: 10)
        XCTAssertLessThan(abs(next.frame.midY - app.frame.midY), app.frame.height * 0.25)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Songs reopens at current song without return bar"; shot.lifetime = .keepAlways; add(shot)
        app.buttons["Pause"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Home"].firstMatch.tap()
        app.buttons["Songs"].firstMatch.tap()
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: first)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(next.isHittable, "A paused selection must not override the normal sorted starting point")
    }
    @MainActor func testCollectionShuffleStartsPlaybackAndReflectsState() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_ROW_PLAYBACK"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Albums"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Albums"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].firstMatch.waitForExistence(timeout: 10))
        app.staticTexts["M!ssundaztood"].firstMatch.tap()
        let shuffle = app.buttons["shuffleCollection"]
        let play = app.buttons["playCollection"]
        XCTAssertTrue(shuffle.waitForExistence(timeout: 10))
        XCTAssertEqual(shuffle.value as? String, "off")
        XCTAssertEqual(shuffle.label, "Shuffle")
        shuffle.tap()
        expectation(for: NSPredicate(format: "value == 'on' AND enabled == true"), evaluatedWith: shuffle)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(play.label, "Pause")
        XCTAssertEqual(shuffle.label, "Shuffle", "The label stays stable when enabled")
        XCTAssertTrue(app.navigationBars["M!ssundaztood"].exists)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Collection shuffle active"; shot.lifetime = .keepAlways; add(shot)
        play.tap()
        expectation(for: NSPredicate(format: "label == 'Play' AND enabled == true"), evaluatedWith: play)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(shuffle.value as? String, "on")
        shuffle.tap()
        expectation(for: NSPredicate(format: "label == 'Pause' AND enabled == true"), evaluatedWith: play)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(shuffle.value as? String, "on", "Resume must retain shuffle")
        app.buttons["expandPlayer"].tap()
        let playerShuffle = app.buttons["Shuffle upcoming songs"]
        XCTAssertTrue(playerShuffle.waitForExistence(timeout: 10))
        XCTAssertEqual(playerShuffle.value as? String, "on")
        playerShuffle.tap()
        expectation(for: NSPredicate(format: "value == 'off'"), evaluatedWith: playerShuffle)
        waitForExpectations(timeout: 10)
        app.buttons["Close player"].tap()
        XCTAssertEqual(shuffle.value as? String, "off", "Both shuffle controls must reflect the same state")
        shuffle.tap()
        expectation(for: NSPredicate(format: "value == 'on' AND enabled == true"), evaluatedWith: shuffle)
        waitForExpectations(timeout: 10)
        shuffle.tap()
        expectation(for: NSPredicate(format: "value == 'off' AND enabled == true"), evaluatedWith: shuffle)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(play.label, "Pause", "Turning shuffle off must not stop playback")
    }
    @MainActor func testCurrentTrackIsMarkedInListAndQueue() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_ROW_PLAYBACK"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Library"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Library"].firstMatch.tap()
        app.buttons["Songs"].firstMatch.tap()
        let current = app.buttons["songRow-1"]
        let other = app.buttons["songRow-2"]
        XCTAssertTrue(current.waitForExistence(timeout: 10))
        XCTAssertEqual(current.value as? String, "Now playing")
        XCTAssertEqual(other.value as? String ?? "", "")
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Current song highlighted in list"; shot.lifetime = .keepAlways; add(shot)
        app.buttons["Pause"].firstMatch.tap()
        let paused = NSPredicate(format: "value == %@", "Paused")
        expectation(for: paused, evaluatedWith: current)
        waitForExpectations(timeout: 10)
        XCTAssertFalse(app.buttons["Close player"].exists, "Playback controls must not open the player")
        let expand = app.buttons["expandPlayer"]
        expand.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.85)).tap()
        XCTAssertTrue(app.buttons["Close player"].waitForExistence(timeout: 5), "The open metadata area must open the player")
        XCTAssertTrue(app.buttons["upNext"].waitForExistence(timeout: 5))
        app.buttons["upNext"].tap()
        let queueCurrent = app.descendants(matching: .any)["queueCurrentTrack"].firstMatch
        XCTAssertTrue(queueCurrent.waitForExistence(timeout: 10))
        XCTAssertEqual(queueCurrent.value as? String, "Paused")
        XCTAssertTrue(app.buttons["Play queued Family Portrait"].exists)
        XCTAssertEqual(app.buttons["Play queued Just Like a Pill"].value as? String ?? "", "", "An upcoming duplicate must not be marked current")
        let queueShot = XCTAttachment(screenshot: app.screenshot()); queueShot.name = "Queue current track and context"; queueShot.lifetime = .keepAlways; add(queueShot)
    }
    @MainActor func testAccountDeletionRequiresConfirmationAndPassword() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Settings"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Settings"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 10))
        let entry = app.descendants(matching: .any)["deleteAccountSettings"].firstMatch
        // SwiftUI can report off-screen Form rows as hittable on iPad.
        // Require the entire row to be visible before tapping it.
        for _ in 0..<8 {
            if entry.exists && entry.isHittable && app.frame.insetBy(dx: 0, dy: 90).contains(entry.frame) { break }
            app.collectionViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(entry.exists && entry.isHittable)
        XCTAssertTrue(app.frame.insetBy(dx: 0, dy: 90).contains(entry.frame))
        entry.tap()
        XCTAssertTrue(app.navigationBars["Delete account"].waitForExistence(timeout: 10))
        let password = app.secureTextFields["deletionPassword"]
        for _ in 0..<5 { if password.exists && password.isHittable { break }; app.collectionViews.firstMatch.swipeUp() }
        XCTAssertTrue(password.exists); password.tap(); password.typeText("wrong")
        let submit = app.buttons["requestAccountDeletion"]
        for _ in 0..<4 { if submit.isHittable { break }; app.collectionViews.firstMatch.swipeUp() }
        submit.tap()
        app.buttons["Cancel"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Delete account"].exists)
        XCTAssertTrue(app.alerts.firstMatch.waitForNonExistence(timeout: 5))
        for _ in 0..<6 {
            if submit.exists && submit.isHittable { break }
            app.collectionViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(submit.exists && submit.isHittable)
        submit.tap(); app.buttons["Permanently delete account"].tap()
        let deletionError = app.staticTexts["deletionError"]
        for _ in 0..<6 {
            if deletionError.waitForExistence(timeout: 1) && deletionError.isHittable { break }
            app.collectionViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(deletionError.exists && deletionError.isHittable)
        for _ in 0..<4 { if password.isHittable { break }; app.collectionViews.firstMatch.swipeDown() }
        password.tap(); password.typeText("fixture")
        for _ in 0..<4 { if submit.isHittable { break }; app.collectionViews.firstMatch.swipeUp() }
        submit.tap(); app.buttons["Permanently delete account"].tap()
        XCTAssertTrue(app.alerts["Account deleted"].waitForExistence(timeout: 10))
        app.alerts["Account deleted"].buttons["OK"].tap()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
    }
    @MainActor func testHomeScrollPositionSurvivesFullScreenPlayerDismissal() {
        let app = XCUIApplication()
        // Larger text gives Home real overflow even on a 13-inch iPad.
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityM"]
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_REMOTE_OWNER"] = "1"
        app.launchEnvironment["ARRIFY_UI_PERSONAL_PLAYLIST"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let title = app.staticTexts["On the record"]
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        let takeover = app.buttons["Play here"]
        XCTAssertTrue(takeover.waitForExistence(timeout: 10))
        let playlist = app.otherElements["collectionCard-playlists-1"]
        let initialTitleY = title.frame.minY
        // Move well away from the top and keep a visible card as a position
        // anchor. Drag inside content, avoiding the sidebar and player footer.
        for _ in 0..<12 {
            if title.frame.minY < initialTitleY - 100 && playlist.isHittable && playlist.frame.maxY < takeover.frame.minY { break }
            let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0.7, dy: 0))
            let start = origin.withOffset(CGVector(dx: 0, dy: takeover.frame.minY - 40))
            let end = origin.withOffset(CGVector(dx: 0, dy: max(180, takeover.frame.minY - 340)))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        XCTAssertLessThan(title.frame.minY, initialTitleY - 100, "Home must be scrolled before opening the player")
        XCTAssertTrue(playlist.isHittable)
        let scrolledY = playlist.frame.minY
        for _ in 0..<2 {
            app.buttons["expandPlayer"].tap()
            let close = app.buttons["Close player"]
            XCTAssertTrue(close.waitForExistence(timeout: 10))
            close.tap()
            XCTAssertTrue(close.waitForNonExistence(timeout: 10))
            expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: playlist)
            waitForExpectations(timeout: 10)
            XCTAssertLessThan(title.frame.minY, initialTitleY - 100, "Minimizing the player must not reset Home to the top")
            XCTAssertEqual(playlist.frame.minY, scrolledY, accuracy: 3,
                           "Home must retain its exact browsing position after player dismissal")
        }
    }
    @MainActor func testHomeRecentSongsAndFullScreenSongActions() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let recent = app.buttons["Play recently played song Just Like a Pill by P!nk"]
        XCTAssertTrue(recent.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Playlists"].exists)
        XCTAssertFalse(app.staticTexts["Set the mood"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["homePlaylistsAll"].exists)
        recent.tap()
        XCTAssertTrue(app.buttons["expandPlayer"].waitForExistence(timeout: 10)); app.buttons["expandPlayer"].tap()
        XCTAssertTrue(app.buttons["More options for Just Like a Pill"].waitForExistence(timeout: 10))
        app.buttons["serverConnection"].tap()
        XCTAssertTrue(app.navigationBars["Server connection"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["connectionStatus"].firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any)["connectionRoute"].firstMatch.exists)
        XCTAssertTrue(app.staticTexts["connectionAddress"].exists)
        XCTAssertFalse(app.buttons["Copy address"].exists)
        let connectionShot = XCTAttachment(screenshot: app.screenshot())
        connectionShot.name = "Server connection details"; connectionShot.lifetime = .keepAlways; add(connectionShot)
        app.buttons["Done"].tap()
        XCTAssertTrue(app.buttons["More options for Just Like a Pill"].isHittable)
        app.buttons["More options for Just Like a Pill"].tap()
        for title in ["Add to Liked songs", "Add to playlist", "Add to queue", "Go to song radio", "Go to artist radio", "Go to artist", "Go to album", "Go to queue", "Browse original album"] {
            XCTAssertTrue(app.buttons[title].exists, title)
        }
        let likeAction = app.buttons.matching(NSPredicate(format: "label == %@", "Add to Liked songs")).firstMatch
        XCTAssertTrue(likeAction.isHittable)
        XCTAssertFalse(likeAction.waitForNonExistence(timeout: 12), "Menu must stay presented across refreshes")
        likeAction.press(forDuration: 0.15)
        XCTAssertTrue(likeAction.waitForNonExistence(timeout: 5), "The song action must dismiss its menu")
        XCTAssertTrue(app.buttons["Remove from Liked songs: Just Like a Pill"].waitForExistence(timeout: 5))
    }
    @MainActor func testEmptyPlayerAndSingleTapAlbumActions() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Browse music"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Choose something to play"].exists)
        let emptyShot = XCTAttachment(screenshot: app.screenshot()); emptyShot.name = "Empty player footer"; emptyShot.lifetime = .keepAlways; add(emptyShot)
        XCTAssertFalse(app.buttons["expandPlayer"].exists)
        app.buttons["Browse music"].tap()
        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 10))
        app.buttons["Home"].firstMatch.tap()
        let recent = app.buttons["Play recently played song Just Like a Pill by P!nk"]
        XCTAssertTrue(recent.waitForExistence(timeout: 10)); recent.tap()
        XCTAssertTrue(app.buttons["expandPlayer"].waitForExistence(timeout: 10)); app.buttons["expandPlayer"].tap()
        for _ in 0..<2 {
            app.buttons["More options for Just Like a Pill"].tap()
            let album = app.buttons["Go to album"]
            XCTAssertTrue(album.waitForExistence(timeout: 5)); album.tap()
            XCTAssertTrue(app.navigationBars["M!ssundaztood"].waitForExistence(timeout: 10))
            app.buttons["Done"].tap()
        }
        app.buttons["More options for Just Like a Pill"].tap()
        let original = app.buttons["Browse original album"]
        XCTAssertTrue(original.waitForExistence(timeout: 5))
        if !original.isHittable { app.swipeUp() }
        XCTAssertTrue(original.isHittable)
        let originalFrame = original.frame
        XCTAssertFalse(original.waitForNonExistence(timeout: 12), "Open menu must survive background refreshes")
        XCTAssertTrue(original.isHittable, "Refresh must not reset the menu scroll position")
        XCTAssertEqual(original.frame.midY, originalFrame.midY, accuracy: 3)
        original.tap()
        XCTAssertTrue(app.navigationBars["Original album"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].waitForExistence(timeout: 10))
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Original album from one tap"; shot.lifetime = .keepAlways; add(shot)
    }

    @MainActor func testSearchPopularityAndSongOptions() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Songs"].firstMatch.tap()
        let popularity = app.buttons["Popularity: 2 of 3. View sources"]
        XCTAssertTrue(popularity.waitForExistence(timeout: 10))
        popularity.tap()
        XCTAssertTrue(app.descendants(matching: .any)["Fixture chart"].firstMatch.waitForExistence(timeout: 5))
        app.buttons["Done"].tap()
        app.buttons["More options for Just Like a Pill"].tap()
        for title in ["Add to Liked songs", "Add to playlist", "Add to queue", "Go to song radio", "Go to artist radio", "Go to artist", "Go to album", "Go to queue", "Browse original album"] {
            XCTAssertTrue(app.buttons[title].exists, title)
        }
        let likeAction = app.buttons.matching(NSPredicate(format: "label == %@", "Add to Liked songs")).firstMatch
        XCTAssertTrue(likeAction.isHittable)
        XCTAssertFalse(likeAction.waitForNonExistence(timeout: 12), "Menu must stay presented across refreshes")
        likeAction.press(forDuration: 0.15)
        XCTAssertTrue(likeAction.waitForNonExistence(timeout: 5), "The song action must dismiss its menu")
        XCTAssertTrue(app.buttons["Remove from Liked songs: Just Like a Pill"].waitForExistence(timeout: 5))
    }
    @MainActor func testTabletNewSearchLeavesPreviousDetail() throws {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let search = app.textFields["tabletSearch"]
        guard search.waitForExistence(timeout: 5) else { throw XCTSkip("iPad search navigation regression") }
        search.tap(); search.typeText("pink")
        app.segmentedControls.buttons["Albums"].tap()
        XCTAssertEqual(search.value as? String, "pink", "Changing scope preserves the query")
        XCTAssertFalse(app.textFields["librarySearch"].exists)
        // EntryRow is exposed as a navigation link; use its album text.
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].firstMatch.waitForExistence(timeout: 10))
        app.staticTexts["M!ssundaztood"].firstMatch.tap()
        XCTAssertTrue(app.buttons["playCollection"].waitForExistence(timeout: 10))
        search.tap(); search.typeText("missing")
        XCTAssertTrue(app.staticTexts["No matching albums"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["playCollection"].exists)
    }
    @MainActor func testSignInIsAccessibleAndDoesNotAutoplay() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["serverAddress"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.textFields["username"].exists)
        XCTAssertTrue(app.secureTextFields["password"].exists)
        XCTAssertFalse(app.buttons["signIn"].isEnabled)
        XCTAssertFalse(app.buttons["Pause"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Arrify sign-in"; attachment.lifetime = .keepAlways
        add(attachment)
    }
    @MainActor func testSignInBrowseSearchAndSignOut() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        let username = app.textFields["username"]
        XCTAssertTrue(username.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["privacyPolicy"].firstMatch.exists)
        username.tap(); username.typeText("listener")
        let password = app.secureTextFields["password"]
        password.tap(); password.typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["On the record"].waitForExistence(timeout: 5))
        let homeShot = XCTAttachment(screenshot: app.screenshot()); homeShot.name = "Artwork-led Home"; homeShot.lifetime = .keepAlways; add(homeShot)
        XCTAssertFalse(app.buttons["refreshLibrary"].exists)
        app.scrollViews.firstMatch.swipeDown()
        XCTAssertFalse(app.staticTexts["Library updated"].exists)
        app.buttons["Songs"].tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Arrify library"; attachment.lifetime = .keepAlways; add(attachment)
        let tabletSearch = app.textFields["tabletSearch"]
        let search = tabletSearch.exists ? tabletSearch : app.searchFields.firstMatch
        if !search.waitForExistence(timeout: 5) {
            app.buttons["Search"].firstMatch.tap()
        }
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap(); search.typeText("missing")
        XCTAssertTrue(app.staticTexts["No matching songs"].waitForExistence(timeout: 10))
        if tabletSearch.exists {
            search.typeText("\n")
        } else {
            let closeSearch = app.buttons["Close"].exists ? app.buttons["Close"] : app.buttons["Cancel"]
            closeSearch.tap()
        }
        app.buttons["Settings"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["privacyPolicy"].firstMatch.exists)
        let signOut = app.buttons["Sign out"]
        for _ in 0..<5 {
            if signOut.exists && signOut.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(signOut.waitForExistence(timeout: 5))
        XCTAssertTrue(signOut.isHittable)
        signOut.tap()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 5))
    }
}

extension LaunchTests {
    @MainActor func testAlbumsChannelsAndMoodNavigation() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Albums"].waitForExistence(timeout: 10))
        app.buttons["Albums"].tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].waitForExistence(timeout: 10))
        app.staticTexts["M!ssundaztood"].firstMatch.tap()
        XCTAssertTrue(app.buttons["playCollection"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["shuffleCollection"].exists)
        let primary = app.buttons["playCollection"]
        primary.tap()
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "Pause"), object: primary)], timeout: 10) == .completed)
        primary.tap()
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "Play"), object: primary)], timeout: 10) == .completed)
        let album = XCTAttachment(screenshot: app.screenshot()); album.name = "Album detail"; album.lifetime = .keepAlways; add(album)
        app.buttons["Channels"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play 80s on 8"].waitForExistence(timeout: 10))
        let channels = XCTAttachment(screenshot: app.screenshot()); channels.name = "Channels"; channels.lifetime = .keepAlways; add(channels)
        app.buttons["Library"].firstMatch.tap()
        app.buttons["Moods"].firstMatch.tap()
        XCTAssertTrue(app.buttons["mood-feel-good"].waitForExistence(timeout: 10))
        app.buttons["mood-feel-good"].tap()
        XCTAssertTrue(app.buttons["Play Feel Good Favorites"].waitForExistence(timeout: 10))
        let mood = XCTAttachment(screenshot: app.screenshot()); mood.name = "Mood playlists"; mood.lifetime = .keepAlways; add(mood)
    }
}

extension LaunchTests {
    @MainActor func testLastChannelClearsMiniPlayerAndRepeatControls() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].waitForExistence(timeout: 10))
        app.buttons["Songs"].tap()
        let play = app.buttons["Play Just Like a Pill by P!nk"]
        XCTAssertTrue(play.waitForExistence(timeout: 10)); play.tap()
        let nowPlaying = app.buttons["expandPlayer"]
        XCTAssertTrue(nowPlaying.waitForExistence(timeout: 10), app.debugDescription)
        app.buttons["Channels"].firstMatch.tap()
        let last = app.buttons["Play Station 12"]
        for _ in 0..<10 {
            if last.exists && last.isHittable && last.frame.maxY < nowPlaying.frame.minY { break }
            app.scrollViews.containing(.button, identifier: "Play Station 12").firstMatch.swipeUp()
        }
        XCTAssertTrue(last.isHittable)
        XCTAssertLessThan(last.frame.maxY, nowPlaying.frame.minY)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Last channel above player"; shot.lifetime = .keepAlways; add(shot)
        nowPlaying.tap()
        let close = app.buttons["Close player"]
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        XCTAssertLessThan(close.frame.minY, app.frame.height * 0.15, "Expanded player must occupy the full screen")
        let repeatButton = app.buttons["Repeat"]
        XCTAssertTrue(repeatButton.waitForExistence(timeout: 10), app.debugDescription)
        if !repeatButton.isHittable { app.scrollViews.firstMatch.swipeUp() }
        let shuffleButton = app.buttons["Shuffle upcoming songs"]
        XCTAssertTrue(shuffleButton.exists)
        XCTAssertEqual(shuffleButton.value as? String, "off")
        shuffleButton.tap()
        expectation(for: NSPredicate(format: "value == 'on'"), evaluatedWith: shuffleButton)
        waitForExpectations(timeout: 5)
        shuffleButton.tap()
        expectation(for: NSPredicate(format: "value == 'off'"), evaluatedWith: shuffleButton)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(repeatButton.value as? String, "off")
        repeatButton.tap()
        expectation(for: NSPredicate(format: "value == 'all'"), evaluatedWith: repeatButton)
        waitForExpectations(timeout: 5)
        XCTAssertLessThan(app.buttons["upNext"].frame.maxY, app.frame.maxY - 12, "Footer must fit above the home indicator")
        if UIDevice.current.userInterfaceIdiom == .pad {
            let outputDescription = app.staticTexts["playerOutputDescription"].firstMatch
            XCTAssertTrue(outputDescription.exists)
            let bottomGap = app.frame.maxY - outputDescription.frame.maxY
            XCTAssertGreaterThan(bottomGap, 16, "Keep output controls clear of the bottom safe area")
            XCTAssertLessThan(bottomGap, 100, "Avoid a large unused strip beneath the iPad player controls")
        }
        let playerShot = XCTAttachment(screenshot: app.screenshot()); playerShot.name = "Redesigned player"; playerShot.lifetime = .keepAlways; add(playerShot)
        app.buttons["upNext"].tap()
        XCTAssertTrue(app.staticTexts["Family Portrait"].waitForExistence(timeout: 5))
        app.buttons["Done"].tap()
        XCTAssertFalse(app.buttons["artworkDisplayButton"].exists)
        app.buttons["Show artwork"].tap()
        XCTAssertTrue(app.buttons["Close artwork"].waitForExistence(timeout: 5))
        app.buttons["Close artwork"].tap()
    }
}

extension LaunchTests {
    @MainActor func testLikedSongsCanBeSavedAndRemoved() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].waitForExistence(timeout: 10)); app.buttons["Songs"].tap()
        let addSong = app.buttons["Add to Liked songs: Just Like a Pill"]
        XCTAssertTrue(addSong.waitForExistence(timeout: 10)); addSong.tap()
        XCTAssertTrue(app.buttons["Remove from Liked songs: Just Like a Pill"].waitForExistence(timeout: 5))
        app.buttons["Library"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Liked songs"].firstMatch.waitForExistence(timeout: 5)); app.buttons["Liked songs"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Liked songs"; shot.lifetime = .keepAlways; add(shot)
        app.buttons["Remove from Liked songs: Just Like a Pill"].tap()
        XCTAssertTrue(app.buttons["Add to Liked songs: Just Like a Pill"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].exists, "Unliking should retain the row until refresh")
        app.buttons["Add to Liked songs: Just Like a Pill"].tap()
        XCTAssertTrue(app.buttons["Remove from Liked songs: Just Like a Pill"].waitForExistence(timeout: 5))
    }
}

extension LaunchTests {
    @MainActor func testTabletSearchKeepsHeaderAndPlayerVisible() throws {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        guard app.textFields["tabletSearch"].waitForExistence(timeout: 5) else { throw XCTSkip("iPad layout only") }
        XCTAssertTrue(app.staticTexts["Another spin"].exists)
        XCTAssertFalse(app.staticTexts["Made for listening"].exists)
        app.buttons["Songs"].firstMatch.tap()
        let play = app.buttons["Play Just Like a Pill by P!nk"]
        XCTAssertTrue(play.waitForExistence(timeout: 10))
        XCTAssertEqual(app.searchFields.count, 0, "iPad uses one persistent header search, not a second navigation-bar field")
        XCTAssertFalse(app.textFields["librarySearch"].exists, "Section search must reuse the header")
        XCTAssertTrue(app.segmentedControls.buttons["Songs"].isSelected)
        play.tap()
        let player = app.otherElements["tabletPlayer"]
        XCTAssertTrue(player.waitForExistence(timeout: 5))
        let volumeShot = XCTAttachment(screenshot: app.screenshot())
        volumeShot.name = "iPad mini-player volume alignment"
        volumeShot.lifetime = .keepAlways
        add(volumeShot)
        let search = app.textFields["tabletSearch"]
        search.tap(); search.typeText("pink")
        XCTAssertTrue(search.isHittable)
        XCTAssertTrue(app.buttons["Home"].firstMatch.isHittable)
        XCTAssertTrue(app.buttons["Previous song"].isHittable, "Keyboard must not replace the tablet player with phone controls")
        XCTAssertLessThan(search.frame.maxY, app.keyboards.firstMatch.frame.minY)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "iPad stable search with keyboard"; shot.lifetime = .keepAlways; add(shot)
    }
    @MainActor func testCreatePlaylist() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Library"].firstMatch.waitForExistence(timeout: 10)); app.buttons["Library"].firstMatch.tap()
        app.buttons["Playlists"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Driving favourites"].waitForExistence(timeout: 5), "Curated playlists remain available outside Home")
        app.buttons["Create playlist"].firstMatch.tap()
        XCTAssertTrue(app.textFields["playlistName"].waitForExistence(timeout: 5))
        app.textFields["playlistName"].tap(); app.textFields["playlistName"].typeText("Evening mix")
        app.buttons["savePlaylist"].tap()
        XCTAssertTrue(app.staticTexts["Evening mix"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Done"].exists)
    }
}


extension LaunchTests {
    @MainActor func testTakeoverFooterClearsAllDestinations() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_REMOTE_OWNER"] = "1"
        app.launchEnvironment["ARRIFY_UI_PERSONAL_PLAYLIST"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let takeover = app.buttons["Play here"]
        XCTAssertTrue(takeover.waitForExistence(timeout: 10))
        func checkBottom(_ element: XCUIElement, _ name: String) {
            for _ in 0..<12 {
                if element.exists && element.isHittable && element.frame.maxY < takeover.frame.minY { break }
                // Start inside content, not over the persistent footer or iPad sidebar.
                let top = app.coordinate(withNormalizedOffset: CGVector(dx: 0.7, dy: 0))
                let start = top.withOffset(CGVector(dx: 0, dy: takeover.frame.minY - 40))
                let end = top.withOffset(CGVector(dx: 0, dy: max(180, takeover.frame.minY - 340)))
                start.press(forDuration: 0.05, thenDragTo: end)
            }
            XCTAssertTrue(element.exists, name)
            XCTAssertTrue(element.isHittable, name)
            XCTAssertLessThan(element.frame.maxY, takeover.frame.minY, name)
            XCTAssertTrue(takeover.isHittable)
            let shot = XCTAttachment(screenshot: app.screenshot())
            shot.name = "Takeover footer — " + name; shot.lifetime = .keepAlways; add(shot)
        }
        checkBottom(app.otherElements["collectionCard-playlists-1"], "Home personal playlist")
        app.buttons["Library"].firstMatch.tap()
        let playlist = app.buttons["libraryPersonal-1"]
        checkBottom(playlist, "Library final playlist")
        playlist.tap()
        let lastSong = app.buttons["Play Try by P!nk"]
        XCTAssertTrue(app.buttons["playCollection"].waitForExistence(timeout: 10))
        checkBottom(lastSong, "Playlist detail final song")
        app.buttons["Home"].firstMatch.tap()
        if !app.textFields["tabletSearch"].exists {
            for _ in 0..<8 where !app.buttons["Albums"].firstMatch.isHittable { app.swipeDown() }
        }
        app.buttons["Albums"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].firstMatch.waitForExistence(timeout: 10))
        app.staticTexts["M!ssundaztood"].firstMatch.tap()
        XCTAssertTrue(app.buttons["playCollection"].waitForExistence(timeout: 10))
        checkBottom(lastSong, "Album detail final song")
        app.buttons["Channels"].firstMatch.tap()
        checkBottom(app.buttons["Play Station 12"], "Channels final station")
        let tabletSearch = app.textFields["tabletSearch"]
        if tabletSearch.exists {
            tabletSearch.tap(); tabletSearch.typeText("pink")
        } else {
            app.buttons["Search"].firstMatch.tap()
            let search = app.searchFields.firstMatch
            XCTAssertTrue(search.waitForExistence(timeout: 5))
            search.tap(); search.typeText("pink")
        }
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        if tabletSearch.exists {
            XCTAssertTrue(takeover.isHittable)
            XCTAssertLessThan(takeover.frame.maxY, app.keyboards.firstMatch.frame.minY)
        } else {
            XCTAssertTrue(app.buttons["expandPlayer"].waitForNonExistence(timeout: 5))
            XCTAssertFalse(takeover.exists, "Phone Search keeps the native keyboard input area clear")
        }
        let keyboardShot = XCTAttachment(screenshot: app.screenshot())
        keyboardShot.name = "Takeover footer — Search keyboard"; keyboardShot.lifetime = .keepAlways; add(keyboardShot)
        if tabletSearch.exists { tabletSearch.typeText("\n") }
        else { app.searchFields.firstMatch.typeText("\n") }
        XCTAssertTrue(takeover.waitForExistence(timeout: 5))
        app.segmentedControls.buttons["Songs"].tap()
        XCTAssertTrue(lastSong.waitForExistence(timeout: 10))
        checkBottom(lastSong, "Search final result")
        if !tabletSearch.exists {
            app.buttons["Close"].tap()
            // Closing input leaves iOS's search-role tab bar collapsed.
            let collapsed = app.tabBars.buttons["Channels"]
            if !app.buttons["Settings"].firstMatch.exists && collapsed.exists { collapsed.tap() }
        }
        XCTAssertTrue(app.buttons["Settings"].firstMatch.waitForExistence(timeout: 5))
        app.buttons["Settings"].firstMatch.tap()
        checkBottom(app.staticTexts["settingsBuildVersion"], "Settings final row")
    }

    @MainActor func testSettingsClearsPlaybackFooterWithLargeText() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_REMOTE_OWNER"] = "1"
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryXXXL"]
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let takeover = app.buttons["Play here"]
        XCTAssertTrue(takeover.waitForExistence(timeout: 10))
        app.buttons["Settings"].firstMatch.tap()
        let version = app.staticTexts["settingsBuildVersion"]
        for _ in 0..<10 {
            if version.exists && version.frame.maxY < takeover.frame.minY { break }
            app.swipeUp()
        }
        XCTAssertTrue(version.isHittable)
        XCTAssertLessThan(version.frame.maxY, takeover.frame.minY)
        XCTAssertTrue(app.buttons["Sign out"].isHittable)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Large-text Settings clears playback footer"; shot.lifetime = .keepAlways; add(shot)
    }

    @MainActor func testRemotePlaybackOffersTakeoverFromBrowsing() {
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_REMOTE_OWNER"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let takeover = app.buttons["Play here"]
        XCTAssertTrue(takeover.waitForExistence(timeout: 10))
        XCTAssertTrue(takeover.isHittable)
        XCTAssertFalse(app.otherElements["localVolumeControl"].exists)
        if app.textFields["tabletSearch"].exists {
            XCTAssertGreaterThan(takeover.frame.minY, app.textFields["tabletSearch"].frame.maxY)
        }
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Remote playback takeover available"; shot.lifetime = .keepAlways; add(shot)
        XCTAssertTrue(app.buttons["expandPlayer"].waitForExistence(timeout: 10)); app.buttons["expandPlayer"].tap()
        let fullTakeover = app.buttons["fullScreenTakeOverPlayback"]
        XCTAssertTrue(fullTakeover.waitForExistence(timeout: 10))
        let visible = XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: fullTakeover)
        XCTAssertEqual(XCTWaiter.wait(for: [visible], timeout: 10), .completed)
        let route = app.descendants(matching: .any)["playerAirPlay"].firstMatch
        XCTAssertTrue(route.exists); XCTAssertFalse(route.isEnabled)
        XCTAssertFalse(app.staticTexts["Adjust volume on the playing device"].exists)
        let fullShot = XCTAttachment(screenshot: app.screenshot()); fullShot.name = "Full-screen remote playback banner"; fullShot.lifetime = .keepAlways; add(fullShot)
        app.buttons["Close player"].tap()
        app.buttons["Settings"].firstMatch.tap()
        let version = app.staticTexts["settingsBuildVersion"]
        for _ in 0..<8 {
            if version.exists && version.frame.maxY < takeover.frame.minY { break }
            app.swipeUp()
        }
        XCTAssertTrue(version.exists)
        XCTAssertTrue(version.isHittable)
        XCTAssertLessThan(version.frame.maxY, takeover.frame.minY, "The last Settings row must scroll above the entire playback footer")
        XCTAssertTrue(app.buttons["Sign out"].isHittable)
        let settingsShot = XCTAttachment(screenshot: app.screenshot())
        settingsShot.name = "Settings clears takeover banner"; settingsShot.lifetime = .keepAlways; add(settingsShot)
        takeover.tap()
        let disappeared = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: takeover)
        XCTAssertEqual(XCTWaiter.wait(for: [disappeared], timeout: 10), .completed)
        // This fixture never supplies playable media. Takeover must succeed,
        // then preparation must time out with truthful, retryable controls.
        // Waiting for this state also avoids depending on simulator speed.
        let status = app.staticTexts["playbackStatusMessage"]
        XCTAssertTrue(status.waitForExistence(timeout: 35))
        XCTAssertEqual(status.label, "This song could not be prepared. Tap Retry or choose another song.")
        XCTAssertTrue(app.buttons["Play"].firstMatch.exists)
        XCTAssertFalse(app.buttons["Pause"].firstMatch.exists)
        XCTAssertFalse(takeover.exists)
        if app.textFields["tabletSearch"].exists {
            XCTAssertTrue(app.buttons["retryPlayback"].isHittable)
            XCTAssertGreaterThan(status.frame.minY, app.textFields["tabletSearch"].frame.maxY)
        }
    }
}

extension LaunchTests {
    @MainActor func testLongTitlePlayerDismissalRestoresSearchResponsiveness() {
        XCUIDevice.shared.orientation = .portrait
        let app = XCUIApplication()
        app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"
        app.launchEnvironment["ARRIFY_UI_LONG_TITLE"] = "1"
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Songs"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Songs"].firstMatch.tap()
        let title = "A Very Long Song Title That Wraps Onto Two Lines in the Expanded Player During Keyboard Restoration"
        let play = app.buttons["Play \(title) by P!nk"]
        XCTAssertTrue(play.waitForExistence(timeout: 10)); play.tap()
        let tabletSearch = app.textFields["tabletSearch"]
        let search = tabletSearch.exists ? tabletSearch : app.searchFields.firstMatch
        if !search.waitForExistence(timeout: 5) { app.buttons["Search"].firstMatch.tap() }
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap(); search.typeText("pink")
        for _ in 0..<3 {
            XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
            // iPhone's system tab accessory is covered while search is editing.
            // Submit there; iPad keeps its player visible above the keyboard.
            if !tabletSearch.exists { search.typeText("\n") }
            let expand = app.buttons["expandPlayer"]
            XCTAssertTrue(expand.waitForExistence(timeout: 5))
            XCTAssertTrue(expand.isHittable)
            expand.tap()
            let close = app.buttons["Close player"]
            XCTAssertTrue(close.waitForExistence(timeout: 5))
            XCTAssertTrue(app.staticTexts[title].firstMatch.exists)
            let pause = app.buttons["expandedPlayPause"]
            let output = app.descendants(matching: .any)["playerAirPlay"].firstMatch
            XCTAssertTrue(pause.exists)
            XCTAssertTrue(output.exists)
            XCTAssertEqual(pause.frame.midX, output.frame.midX, accuracy: 1, "Output picker must be centered beneath play/pause")
            close.tap()
            XCTAssertTrue(close.waitForNonExistence(timeout: 5), "Wait for full-screen dismissal before focusing the underlying search")
            XCTAssertTrue(search.waitForExistence(timeout: 5))
            XCTAssertTrue(search.isHittable, "Closing the long-title player must return a responsive search UI")
            search.tap()
            XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        }
        // The fixture intentionally never finishes preparation. Its timeout
        // banner must not cover the navigation search field or intercept input.
        if !tabletSearch.exists {
            let status = app.staticTexts["playbackStatusMessage"]
            XCTAssertTrue(status.waitForExistence(timeout: 35))
            XCTAssertGreaterThan(status.frame.minY, search.frame.maxY)
            search.tap()
        }
        // An actual interaction, not just an existence query, verifies the UI
        // still services input after keyboard restoration and dismissal.
        search.typeText("missing")
        XCTAssertTrue(app.staticTexts["No matching songs"].waitForExistence(timeout: 10))
    }
}


extension LaunchTests {
    @MainActor func testCreateAccountShowsApprovalAndReturnsToSignIn() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        let create = app.buttons["createAccount"]
        for _ in 0..<4 {
            if create.exists && create.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(create.exists && create.isHittable)
        create.tap()
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "administrator")).firstMatch.exists)
        app.textFields["newUsername"].tap(); app.textFields["newUsername"].typeText("newlistener")
        app.textFields["newEmail"].tap(); app.textFields["newEmail"].typeText("new@example.invalid")
        app.secureTextFields["newPassword"].tap(); app.secureTextFields["newPassword"].typeText("long-new-password")
        let confirmation = app.secureTextFields["confirmPassword"]
        for _ in 0..<5 {
            if confirmation.exists && confirmation.isHittable { break }
            if app.collectionViews.firstMatch.exists { app.collectionViews.firstMatch.swipeUp() }
            else { app.swipeUp() }
        }
        XCTAssertTrue(confirmation.exists && confirmation.isHittable)
        confirmation.tap(); confirmation.typeText("long-new-password")
        if !app.buttons["submitAccount"].isHittable { app.swipeUp() }
        app.buttons["submitAccount"].tap()
        XCTAssertTrue(app.staticTexts["registrationMessage"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["registrationMessage"].label.contains("approve"))
        app.buttons["Return to sign in"].tap()
        XCTAssertTrue(app.buttons["signIn"].waitForExistence(timeout: 5))
    }
    @MainActor func testBrowseCategoriesChartsAndRequests() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        XCTAssertTrue(app.buttons["Library"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["Library"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Browse"].firstMatch.waitForExistence(timeout: 10)); app.buttons["Browse"].firstMatch.tap()
        for _ in 0..<5 {
            if app.staticTexts["Pop"].exists && app.staticTexts["Pop"].firstMatch.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(app.buttons["Pop, 2 songs"].waitForExistence(timeout: 5) || app.staticTexts["Pop"].exists)
        app.staticTexts["Pop"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["Albums"].tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.firstMatch.tap()
        let hot = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "What’s Hot,")).firstMatch
        for _ in 0..<5 {
            if hot.exists && hot.isHittable { break }
            app.swipeDown()
        }
        XCTAssertTrue(hot.isHittable); hot.tap()
        XCTAssertTrue(app.staticTexts["Unavailable fixture song"].waitForExistence(timeout: 10))
        app.buttons["Request"].tap()
        XCTAssertTrue(app.buttons["Requested"].waitForExistence(timeout: 10))
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Native Browse chart"; shot.lifetime = .keepAlways; add(shot)
    }
    @MainActor func testLibraryTabAlwaysReturnsToRoot() {
        let app = XCUIApplication(); app.launchEnvironment["ARRIFY_UI_TESTING"] = "1"; app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
        app.textFields["username"].tap(); app.textFields["username"].typeText("listener")
        app.secureTextFields["password"].tap(); app.secureTextFields["password"].typeText("fixture")
        app.buttons["signIn"].tap()
        let library = app.buttons["Library"].firstMatch
        XCTAssertTrue(library.waitForExistence(timeout: 10)); library.tap()
        let browse = app.buttons["Browse"].firstMatch
        XCTAssertTrue(browse.waitForExistence(timeout: 5)); browse.tap()
        library.tap()
        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Browse"].firstMatch.exists)
    }

}
