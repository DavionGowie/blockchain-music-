// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract SongToken is ERC20, Ownable {
    constructor(string memory name, string memory symbol, address creator) ERC20(name, symbol) Ownable(creator) {
        _mint(creator, 1000000 * 10**decimals()); // 1M tokens initially to creator
    }
}

contract SmartMusic is Ownable {

    uint256 public platformRoyaltyFee = 0.05 ether;
    uint256 public constant DEFAULT_INVESTMENT_PRICE = 0.00001 ether;

    struct Song {
        uint id;
        string title;
        string artist;
        string spotifyId;
        address owner;
        uint downloadPrice;
        uint investmentPrice;
        uint fullRightsPrice;
        address investmentToken;
        bool investmentEnabled;
        bool fullRightsEnabled;
        bool exists;
    }

    mapping(uint => Song) public songs;
    mapping(address => mapping(uint => bool)) public downloadRights; // user => songId => hasDownloadRight
    uint public nextSongId = 1;

    event SongAdded(uint indexed songId, string title, address indexed owner);
    event DownloadPurchased(uint indexed songId, address indexed buyer);
    event InvestmentMade(uint indexed songId, address indexed investor, uint amount);
    event RightsPurchased(uint indexed songId, address indexed newOwner);
    event PlatformRoyaltyPaid(uint indexed songId, address indexed payer, uint256 feeAmount, address indexed platformOwner);

    constructor() Ownable(msg.sender) {}

    function setPlatformRoyaltyFee(uint256 _newFee) external onlyOwner {
        platformRoyaltyFee = _newFee;
    }

    function _distributePayment(uint _songId, address songOwner, uint256 baseAmount) internal {
        uint256 totalRequired = baseAmount + platformRoyaltyFee;
        require(msg.value >= totalRequired, "Not enough AVAX including platform royalty");

        payable(owner()).transfer(platformRoyaltyFee);
        payable(songOwner).transfer(baseAmount);

        uint256 refund = msg.value - totalRequired;
        if (refund > 0) {
            payable(msg.sender).transfer(refund);
        }

        emit PlatformRoyaltyPaid(_songId, msg.sender, platformRoyaltyFee, owner());
    }

    function addSongFromSpotify(
        string memory _title,
        string memory _artist,
        string memory _spotifyId,
        uint _downloadPrice,
        uint _investmentPrice,
        uint _fullRightsPrice,
        bool _investmentEnabled,
        bool _fullRightsEnabled
    ) public onlyOwner {
        uint songId = nextSongId++;
        songs[songId] = Song({
            id: songId,
            title: _title,
            artist: _artist,
            spotifyId: _spotifyId,
            owner: msg.sender,
            downloadPrice: _downloadPrice,
            investmentPrice: _investmentPrice,
            fullRightsPrice: _fullRightsPrice,
            investmentToken: address(0),
            investmentEnabled: _investmentEnabled,
            fullRightsEnabled: _fullRightsEnabled,
            exists: true
        });

        emit SongAdded(songId, _title, msg.sender);
    }

    function createInvestmentToken(uint _songId, string memory _tokenName, string memory _tokenSymbol) public {
        require(songs[_songId].exists, "Song does not exist");
        require(songs[_songId].owner == msg.sender, "Only owner can create investment token");
        require(songs[_songId].investmentEnabled, "Investment not enabled for this song");
        require(songs[_songId].investmentToken == address(0), "Investment token already exists");

        SongToken token = new SongToken(_tokenName, _tokenSymbol, msg.sender);
        songs[_songId].investmentToken = address(token);
    }

    function buyDownloadRights(uint _songId) public payable {
        Song memory song = songs[_songId];
        require(song.exists, "Song does not exist");
        require(!downloadRights[msg.sender][_songId], "Already owns download rights");

        _distributePayment(_songId, song.owner, song.downloadPrice);
        downloadRights[msg.sender][_songId] = true;

        emit DownloadPurchased(_songId, msg.sender);
    }

    function investInSong(uint _songId, uint _tokenAmount) public payable {
        Song memory song = songs[_songId];
        require(song.exists, "Song does not exist");
        require(song.investmentEnabled, "Investment not enabled for this song");
        require(song.investmentToken != address(0), "Investment token not created");
        uint256 investmentAmount = _tokenAmount * song.investmentPrice;
        require(msg.value >= investmentAmount + platformRoyaltyFee, "Not enough AVAX including platform royalty");

        SongToken token = SongToken(song.investmentToken);
        require(token.balanceOf(song.owner) >= _tokenAmount * 10**18, "Not enough tokens available");

        _distributePayment(_songId, song.owner, investmentAmount);
        token.transferFrom(song.owner, msg.sender, _tokenAmount * 10**18);

        emit InvestmentMade(_songId, msg.sender, _tokenAmount);
    }

    function buyFullRights(uint _songId) public payable {
        Song memory song = songs[_songId];
        require(song.exists, "Song does not exist");
        require(song.fullRightsEnabled, "Full rights purchase not enabled for this song");

        _distributePayment(_songId, song.owner, song.fullRightsPrice);
        songs[_songId].owner = msg.sender;

        emit RightsPurchased(_songId, msg.sender);
    }

    function getSong(uint _songId) public view returns (
        uint id,
        string memory title,
        string memory artist,
        string memory spotifyId,
        address owner,
        uint downloadPrice,
        uint investmentPrice,
        uint fullRightsPrice,
        address investmentToken,
        bool investmentEnabled,
        bool fullRightsEnabled,
        bool exists
    ) {
        Song memory song = songs[_songId];
        return (
            song.id,
            song.title,
            song.artist,
            song.spotifyId,
            song.owner,
            song.downloadPrice,
            song.investmentPrice,
            song.fullRightsPrice,
            song.investmentToken,
            song.investmentEnabled,
            song.fullRightsEnabled,
            song.exists
        );
    }

    function hasDownloadRights(address _user, uint _songId) public view returns (bool) {
        return downloadRights[_user][_songId];
    }
}