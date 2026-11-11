import XCTest
final class LaunchTests: XCTestCase {
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
        let entry = app.descendants(matching: .any)["deleteAccountSettings"].firstMatch
        for _ in 0..<8 { if entry.exists && entry.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(entry.exists && entry.isHittable); entry.tap()
        let password = app.secureTextFields["deletionPassword"]
        for _ in 0..<5 { if password.exists && password.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(password.exists); password.tap(); password.typeText("wrong")
        let submit = app.buttons["requestAccountDeletion"]
        for _ in 0..<4 { if submit.isHittable { break }; app.swipeUp() }
        submit.tap()
        app.buttons["Cancel"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Delete account"].exists)
        submit.tap(); app.buttons["Permanently delete account"].tap()
        XCTAssertTrue(app.staticTexts["deletionError"].waitForExistence(timeout: 10))
        for _ in 0..<4 { if password.isHittable { break }; app.swipeDown() }
        password.tap(); password.typeText("fixture")
        for _ in 0..<4 { if submit.isHittable { break }; app.swipeUp() }
        submit.tap(); app.buttons["Permanently delete account"].tap()
        XCTAssertTrue(app.alerts["Account deleted"].waitForExistence(timeout: 10))
        app.alerts["Account deleted"].buttons["OK"].tap()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout: 10))
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
        app.buttons["Add to Liked songs"].tap()
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
        XCTAssertTrue(app.staticTexts["On the record."].waitForExistence(timeout: 5))
        let homeShot = XCTAttachment(screenshot: app.screenshot()); homeShot.name = "Artwork-led Home"; homeShot.lifetime = .keepAlways; add(homeShot)
        app.buttons["refreshLibrary"].tap()
        XCTAssertFalse(app.staticTexts["Library updated"].exists)
        app.buttons["Songs"].tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Arrify library"; attachment.lifetime = .keepAlways; add(attachment)
        let search = app.searchFields.firstMatch
        if !search.waitForExistence(timeout: 5) {
            app.buttons["Search"].firstMatch.tap()
        }
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap(); search.typeText("missing")
        XCTAssertTrue(app.staticTexts["No matching songs"].waitForExistence(timeout: 10))
        let closeSearch = app.buttons["Close"].exists ? app.buttons["Close"] : app.buttons["Cancel"]
        closeSearch.tap()
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
        XCTAssertTrue(app.buttons["Shuffle upcoming songs"].exists)
        XCTAssertEqual(repeatButton.value as? String, "off")
        repeatButton.tap()
        expectation(for: NSPredicate(format: "value == 'all'"), evaluatedWith: repeatButton)
        waitForExpectations(timeout: 5)
        XCTAssertLessThan(app.buttons["upNext"].frame.maxY, app.frame.maxY - 12, "Footer must fit above the home indicator")
        let playerShot = XCTAttachment(screenshot: app.screenshot()); playerShot.name = "Redesigned player"; playerShot.lifetime = .keepAlways; add(playerShot)
        app.buttons["upNext"].tap()
        XCTAssertTrue(app.staticTexts["Family Portrait"].waitForExistence(timeout: 5))
        app.buttons["Done"].tap()
        app.buttons["artworkDisplayButton"].tap()
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
        XCTAssertTrue(app.buttons["Liked songs"].waitForExistence(timeout: 5)); app.buttons["Liked songs"].tap()
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
        play.tap()
        let player = app.otherElements["tabletPlayer"]
        XCTAssertTrue(player.waitForExistence(timeout: 5))
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
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Remote playback takeover available"; shot.lifetime = .keepAlways; add(shot)
        takeover.tap()
        let disappeared = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: takeover)
        XCTAssertEqual(XCTWaiter.wait(for: [disappeared], timeout: 10), .completed)
        XCTAssertTrue(app.buttons["Pause"].firstMatch.exists)
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
            XCTAssertTrue(search.waitForExistence(timeout: 5))
            XCTAssertTrue(search.isHittable, "Closing the long-title player must return a responsive search UI")
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
        app.textFields["newUsername"].tap(); app.textFields["newUsername"].typeText("newlistener")
        app.textFields["newEmail"].tap(); app.textFields["newEmail"].typeText("new@example.invalid")
        app.secureTextFields["newPassword"].tap(); app.secureTextFields["newPassword"].typeText("long-new-password")
        app.secureTextFields["confirmPassword"].tap(); app.secureTextFields["confirmPassword"].typeText("long-new-password")
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
        XCTAssertTrue(app.buttons["Browse"].firstMatch.waitForExistence(timeout: 10)); app.buttons["Browse"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Pop, 2 songs"].waitForExistence(timeout: 5) || app.staticTexts["Pop"].exists)
        app.staticTexts["Pop"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Play Just Like a Pill by P!nk"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["Albums"].tap()
        XCTAssertTrue(app.staticTexts["M!ssundaztood"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.firstMatch.tap()
        app.buttons["What’s Hot"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Unavailable fixture song"].waitForExistence(timeout: 10))
        app.buttons["Request"].tap()
        XCTAssertTrue(app.buttons["Requested"].waitForExistence(timeout: 10))
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = "Native Browse chart"; shot.lifetime = .keepAlways; add(shot)
    }
}
